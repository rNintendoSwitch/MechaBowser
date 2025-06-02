import calendar
import collections
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Generator, Literal, Optional, Tuple, Union

import aiohttp
import config  # type: ignore
import discord
import pymongo
import token_bucket
from dateutil import parser
from discord import app_commands
from discord.ext import commands, tasks
from fuzzywuzzy import fuzz

import tools  # type: ignore


mclient = pymongo.MongoClient(config.mongoURI)

GIANTBOMB_NSW_ID = 157
AUTO_SYNC = False
SEARCH_RATIO_THRESHOLD = 50


class RatelimitException(Exception):
    pass


class GiantBomb:
    def __init__(self, api_key):
        self.BASE_URL = 'https://www.giantbomb.com/api'
        self.api_key = api_key

        # Ratelimit burst limit 200, renews at 200 / 1hr
        self.bucket_storage = token_bucket.MemoryStorage()
        self.ratelimit = token_bucket.Limiter(200 / (60 * 60), 200, self.bucket_storage)

    def raise_for_ratelimit(self, resource: str):
        if '/' in resource:
            raise ValueError(f'malformed resource: {resource}')

        rate_limited = not self.ratelimit.consume(resource)
        if rate_limited:
            raise RatelimitException()

    async def fetch_items(
        self, path: Literal['games', 'releases'], after: datetime = None
    ) -> Generator[dict, None, None]:  # type: ignore
        if path not in ['games', 'releases']:
            raise ValueError(f'invalid path: {path}')

        offset = 0

        for _ in range(1, 1000):
            async with aiohttp.ClientSession() as session:
                self.raise_for_ratelimit(path)

                params = {
                    'api_key': self.api_key,
                    'format': 'json',
                    'limit': 100,
                    'offset': offset,
                    'sort': 'date_last_updated:asc',
                }

                # There is a bug in the GiantBomb API where if we want to fliter a platform and want to use another
                # filter, we must place the platform in the filter key instead of using the platforms key.
                # https://www.giantbomb.com/forums/api-developers-3017/unable-to-filter-games-by-date-added-1794952/#js-message-8288158
                #
                # Futhermore, confusingly, both the /games and /releases have a platforms key, however their filter
                # subkey is either 'platform' or 'platforms', respectfully.
                if after:
                    after = after + timedelta(0, 1)  # Add 1 sec
                    start = after.isoformat(" ", timespec="seconds")
                    end = "2100-01-01 00:00:00"
                    platform_s = 'platform' if path == 'releases' else 'platforms'
                    params['filter'] = f'date_last_updated:{start}|{end},{platform_s}:{GIANTBOMB_NSW_ID}'
                else:
                    params['platforms'] = GIANTBOMB_NSW_ID

                async with session.get(f'{self.BASE_URL}/{path}', params=params) as resp:
                    resp.raise_for_status()
                    resp_json = await resp.json()

                    for item in resp_json['results']:
                        yield item

                    offset += resp_json['number_of_page_results']
                    if offset >= int(resp_json['number_of_total_results']):  # releases returns this as a str
                        break  # no more results

    async def fetch_item(self, path: Literal['game', 'release'], guid: str) -> Optional[dict]:
        if path not in ['game', 'release']:
            raise ValueError(f'invalid path: {path}')

        async with aiohttp.ClientSession() as session:
            self.raise_for_ratelimit(path)

            params = {'api_key': self.api_key, 'format': 'json'}
            async with session.get(f'{self.BASE_URL}/{path}/{guid}', params=params) as resp:
                resp.raise_for_status()
                resp_json = await resp.json()

                return resp_json['results'] if resp_json['results'] else None


class Games(commands.Cog, name='Games'):
    def __init__(self, bot):
        self.bot = bot
        self.GiantBomb = GiantBomb(config.giantbomb)
        self.db = mclient.bowser.games

        self.last_sync = {
            'part': {'at': None, 'count': {'games': 0, 'releases': 0}, 'running': False},
            'full': {'at': None, 'count': {'games': 0, 'releases': 0}, 'running': False},
        }

        # Ensure indices exist
        self.db.create_index([("date_last_updated", pymongo.DESCENDING)])
        self.db.create_index([("guid", pymongo.ASCENDING)], unique=True)
        self.db.create_index([("game.id", pymongo.ASCENDING)])

        # Generate the pipeline
        self.pipeline = [
            {'$match': {'_type': 'game'}},  # Select games
            {
                '$graphLookup': {
                    'from': 'games',
                    'startWith': '$id',
                    'connectFromField': 'id',
                    'connectToField': 'game.id',
                    'as': '_releases',
                    'restrictSearchWithMatch': {'_type': 'release'},
                }
            },  # Search for releases from 'id' to release 'game.id' field, and add as '_releases'
            {
                '$project': {
                    'guid': 1,
                    'name': 1,
                    'aliases': 1,
                    'original_release_date': 1,
                    '_releases.name': 1,
                    '_releases.release_date': 1,
                }
            },  # Filter to only stuff we want
        ]
        self.aggregatePipeline = list(self.db.aggregate(self.pipeline))

        if AUTO_SYNC:
            self.sync_db.start()  # pylint: disable=no-member

    async def cog_unload(self):
        if AUTO_SYNC:
            self.sync_db.cancel()  # pylint: disable=no-member

    @tasks.loop(hours=1)
    async def sync_db(self, force_full: bool = False) -> Tuple[int, str]:
        # If last full sync was more then a day ago (or on restart/forced), preform a new full sync
        day_ago = datetime.now(tz=timezone.utc) - timedelta(days=1)
        full = force_full or ((self.last_sync['full']['at'] < day_ago) if self.last_sync['full']['at'] else True)

        if not full:
            try:
                latest_doc = self.db.find().sort("date_last_updated", pymongo.DESCENDING).limit(1).next()
                after = latest_doc['date_last_updated']
            except StopIteration:
                full = True  # Do full sync if we're having issues getting latest updated

        detail_str = '(full)' if full else f'(partial after {after})'
        logging.info(f'[Games] Syncing games database {detail_str}...')
        self.last_sync['full' if full else 'part']['running'] = True

        if full:
            # Flag items so we can detect if they are not updated.
            self.db.update_many({}, {'$set': {'_full_sync_updated': False}})

        count = {}
        for type, path in [('game', 'games'), ('release', 'releases')]:
            count[path] = 0
            try:
                async for game in self.GiantBomb.fetch_items(path, None if full else after):
                    if full:
                        game['_full_sync_updated'] = True

                    self.update_item_in_db(type, game)
                    count[path] += 1

            except aiohttp.ClientResponseError as e:
                if e.status in [429, 420]:  # Giantbomb uses 420 as ratelimiting
                    logging.error('[Games] Ratelimited with GiantBomb, attempting retry at next loop')
                    return

            except Exception as e:
                logging.error(f'[Games] Exception while syncing games: {e}')
                raise

        if full:
            self.db.delete_many({'_full_sync_updated': False})  # If items were not updated, delete them

        logging.info(f'[Games] Finished syncing {count["games"]} games and {count["releases"]} releases {detail_str}')
        self.last_sync['full' if full else 'part'] = {
            'at': datetime.now(tz=timezone.utc),
            'count': count,
            'running': False,
        }
        self.aggregatePipeline = list(self.db.aggregate(self.pipeline))

        return count, detail_str

    def update_item_in_db(self, type: Literal['game', 'release'], game: dict):
        if type not in ['game', 'release']:
            raise ValueError(f'invalid type: {type}')

        date_keys = {
            'game': ['date_added', 'date_last_updated', 'original_release_date'],
            'release': ['date_added', 'date_last_updated', 'release_date'],
        }

        for key in date_keys[type]:  # Parse dates
            if game[key]:
                game[key] = parser.parse(game[key])

        if type == 'game' and game['aliases']:
            game['aliases'] = game['aliases'].splitlines()

        if type == 'release':
            game['_gameid'] = game['game']['id']

        game['_type'] = type

        return self.db.replace_one({'guid': game['guid']}, game, upsert=True)

    def search(self, query: str) -> Optional[dict]:
        match = {'guid': None, 'score': None, 'name': None}
        for game in self.aggregatePipeline:
            names = collections.Counter([game['name']])
            if game['aliases']:
                names.update(game['aliases'])
            if game['_releases']:
                names.update([release['name'] for release in game['_releases']])

            for name in names:
                methods = [fuzz.ratio, fuzz.partial_ratio, fuzz.token_sort_ratio, fuzz.token_set_ratio]
                rem_punc = re.compile('[^0-9a-zA-Z ]+')

                # Remove punctuation and casing for name and query
                scores = [method(rem_punc.sub('', name.lower()), rem_punc.sub('', query.lower())) for method in methods]
                score = sum(scores) / len(methods)

                if not match['score'] or (score > match['score']):
                    match = {'guid': game['guid'], 'score': score, 'name': name}

        if match['score'] < SEARCH_RATIO_THRESHOLD:
            return None

        return match

    def get_preferred_name(self, guid: str) -> Optional[str]:
        game = self.db.find_one({'_type': 'game', 'guid': guid}, projection={'name': 1, 'id': 1})
        if not game:
            return None

        releases_cursor = self.db.find({'_type': 'release', 'game.id': game['id']}, projection={'name': 1})
        release_names = [release['name'] for release in list(releases_cursor)] if releases_cursor else []

        if not release_names:
            return game['name']

        names = [re.sub('[^0-9a-zA-Z ]+', '', r.lower()) for r in release_names]  # Make lowercase and strip puncts.
        words = [n.split(' ') for n in names]
        shortest = min(words, key=len)

        if any([name[: len(shortest)] != shortest for name in words]):
            return game['name']

        # Access the words in the name of a release to preserve casing and punctuation
        str = ' '.join(release_names[0].split(' ')[: len(shortest)])
        str = re.sub(r' \(Digital\)$', '', str)  # Remove end digital
        str = re.sub(':$', '', str)  # Remove string end colons
        return str

    def parse_expected_release_date(self, item: dict, string: bool = False) -> Union[str, datetime, None]:
        if item is None:
            return None

        if 'original_release_date' in item and item['original_release_date']:  # Games
            return None

        if 'release_date' in item and item['release_date']:  # Releases
            return None

        year = item['expected_release_year']
        month = item['expected_release_month']
        quarter = item['expected_release_quarter']
        day = item['expected_release_day']

        if not year:
            return None

        # Has year...
        if not month:
            if not quarter:
                # Year only:
                return f'{year}' if string else datetime(year, 12, 31)

            # Year and quarter, but no month:
            QUARTER_END_DATES = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
            quarter_end = QUARTER_END_DATES[quarter]
            return f'Q{quarter} {year}' if string else datetime(year, quarter_end[0], quarter_end[1])

        # Has month and year...
        if not day:
            # Has year and month, but no day:
            last_day = calendar.monthrange(year, month)[1]
            return f'{calendar.month_abbr[month]}. {year}' if string else datetime(year, month, last_day)

        # Has month, day, and year:
        return f'{calendar.month_abbr[month]}. {day}, {year}' if string else datetime(year, month, day)

    async def get_image(self, guid: str, type: str, as_url: bool = False) -> Union[str, None]:
        game = self.db.find_one({'_type': 'game', 'guid': guid}, projection={'image': 1})

        if not game or 'image' not in game or type not in game['image']:
            return None

        url = game['image'][type]

        if 'gb_default' in url:
            return None

        if as_url:
            return url

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
                return io.BytesIO(data)

    async def fetch_developers_publishers(
        self, type: Literal['games', 'releases'], guid: str
    ) -> Tuple[Optional[list], Optional[list]]:
        if type not in ['game', 'release']:
            raise ValueError(f'invalid type: {type}')

        db_item = self.db.find_one({'_type': type, 'guid': guid}, projection={'_developers': 1, '_publishers': 1})

        if not db_item:
            return None, None

        if '_developers' in db_item and '_publishers' in db_item:  # Already cached
            return db_item['_developers'], db_item['_publishers']

        item_details = await self.GiantBomb.fetch_item(type, guid)
        developers = item_details['developers'] if 'developers' in item_details else []
        publishers = item_details['publishers'] if 'publishers' in item_details else []

        self.db.update_one(
            {'_type': type, 'guid': guid}, {'$set': {'_developers': developers, '_publishers': publishers}}
        )

        return developers, publishers

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    class GamesCommand(app_commands.Group):
        pass

    games_group = GamesCommand(name='game', description='Find out information about games for the Nintendo Switch!')

    async def _games_search_autocomplete(self, interaction: discord.Interaction, current: str):
        if current:
            game = self.search(current)

        else:
            # Current textbox is empty
            return []

        if game:
            return [app_commands.Choice(name=game['name'], value=game['guid'])]

        else:
            return []

    @games_group.command(name='search')
    @app_commands.describe(query='The term you want to search for a game')
    # @app_commands.autocomplete(query=_games_search_autocomplete)
    @app_commands.checks.cooldown(2, 60, key=lambda i: (i.guild_id, i.user.id))
    async def _games_search(self, interaction: discord.Interaction, query: str):
        '''Search for Nintendo Switch games'''
        await interaction.response.defer()
        user_guid = self.db.find_one({'guid': query.strip()})
        game = None

        if user_guid:
            game = user_guid  # User clicked an autocomplete, giving us the exact guid
            result = {'guid': user_guid['guid'], 'score': 100.0, 'name': user_guid['name']}

        else:
            result = self.search(query)

        if not user_guid and result and result['guid']:
            game = self.db.find_one({'_type': 'game', 'guid': result['guid']})

        if game:
            name = self.get_preferred_name(result['guid'])

            embed = discord.Embed(
                title=name,
                description=game["deck"],
                url=game['site_detail_url'],
                timestamp=game['date_last_updated'],
            )
            embed.set_author(
                name='Data via GiantBomb',
                url=f'https://www.giantbomb.com/games/?game_filter[platform]={GIANTBOMB_NSW_ID}',
                icon_url='https://www.giantbomb.com/a/bundles/giantbombsite/images/win8pin.png',
            )

            image = await self.get_image(result['guid'], 'small_url', as_url=True)
            if image:
                embed.set_thumbnail(url=image)

            # Build footer; if an match was an alias/release name, add it to footer
            has_alias = (result['name'] != name) and (result['name'] != game['name'])
            alias_str = (' ("' + result['name'] + '")') if has_alias else ''

            embed.set_footer(text=f'{result["score"] }% confident{alias_str} ⯁ Entry last updated')

            # Build developers/publisher info
            try:
                gme_devs, gme_pubs = await self.fetch_developers_publishers('game', result['guid'])
                gme_dev_str = ', '.join([x["name"] for x in gme_devs or [{'name': '*Unknown*'}]])
                gme_pub_str = ', '.join([x["name"] for x in gme_pubs or [{'name': '*Unknown*'}]])

                game_desc = f'**Developer{"" if len(gme_devs or []) == 1 else "s"}:** {gme_dev_str}'
                game_desc += f'\n**Publisher{"" if len(gme_pubs or []) == 1 else "s"}:** {gme_pub_str}'
            except RatelimitException:
                game_desc = f'**Developers:** *Unavailable*'
                game_desc += f'\n**Publishers:** *Unavailable*'

            # Build release date line
            if self.parse_expected_release_date(game):
                game_desc += f'\n**Expected Release Date:** {self.parse_expected_release_date(game, True)}'
            elif game["original_release_date"]:
                game_desc += f'\n**Release Date:** {game["original_release_date"].strftime("%b. %d, %Y")}'
            else:
                game_desc += f'\n**Release Date:** *Unknown*'

            if name != game['name']:  # Our preferred name is not actual name
                game_desc = f'**Common title:** {game["name"]}\n{game_desc}'

            embed.add_field(name=f'General Game Details', value=game_desc, inline=False)

            # Build info about switch releases
            release_count = self.db.count({'_type': 'release', 'game.id': game['id']})
            if release_count:
                releases = self.db.find({'_type': 'release', 'game.id': game['id']})

                dates = {'oldest': None, 'newest': None}
                ratelimited = False
                dev_counter = collections.Counter()
                pub_counter = collections.Counter()

                for release in releases:
                    release['_date'] = release['release_date'] or self.parse_expected_release_date(release)

                    if release['_date']:
                        if not dates['oldest']:
                            dates['oldest'] = release
                            dates['newest'] = release

                        if release['_date'] < dates['oldest']['_date']:
                            dates['oldest'] = release

                        if release['_date'] > dates['newest']['_date']:
                            dates['newest'] = release

                    try:
                        rel_devs, rel_pubs = await self.fetch_developers_publishers('release', release['guid'])
                        dev_counter.update([x["name"] for x in rel_devs or []])
                        pub_counter.update([x["name"] for x in rel_pubs or []])

                    except RatelimitException:
                        ratelimited = True

                switch_desc = (
                    f'[**{release_count} known Nintendo Switch release{("" if release_count == 1 else "s")}**]'
                    f'({game["site_detail_url"]}releases)'
                )

                # Render developers/publisher info
                if not ratelimited:
                    devs_common = [n[0] for n in dev_counter.most_common()] or ['*Unknown*']
                    pubs_common = [n[0] for n in pub_counter.most_common()] or ['*Unknown*']

                    switch_desc += f'\n**Developer{"" if len(devs_common) == 1 else "s"}:** {", ".join(devs_common)}'
                    switch_desc += f'\n**Publisher{"" if len(pubs_common) == 1 else "s"}:** {", ".join(pubs_common)}'
                else:
                    switch_desc += f'\n**Developers:** *Unavailable*'
                    switch_desc += f'\n**Publishers:** *Unavailable*'

                # Build release date line
                date_strs = {}
                for key, release in dates.items():
                    if release:
                        if self.parse_expected_release_date(release):
                            date_strs[key] = self.parse_expected_release_date(release, True)
                        elif release["release_date"]:
                            date_strs[key] = release["release_date"].strftime("%b. %d, %Y")
                        else:
                            date_strs[key] = "*Unknown*"
                    else:
                        date_strs[key] = "*Unknown*"

                if dates['newest'] == dates['oldest']:  # Only 1 date
                    expected_prefix = 'Expected ' if self.parse_expected_release_date(dates['oldest']) else ""
                    switch_desc += f'\n**{expected_prefix}Release Date:** {date_strs["oldest"]}'

                else:
                    EXPECTED_PREFIXES = {0: '', 1: '(Expected) ', 2: 'Expected '}
                    expected_count = [bool(self.parse_expected_release_date(x)) for _, x in dates.items()].count(True)
                    expected_prefix = EXPECTED_PREFIXES[expected_count]
                    date_str = f'{date_strs["oldest"]} - {date_strs["newest"]}'

                    switch_desc += f'\n**{expected_prefix}Release Dates:** {date_str}'

                embed.add_field(name=f'Nintendo Switch Releases', value=switch_desc, inline=False)

            return await interaction.followup.send(embed=embed)

        else:
            return await interaction.followup.send(f'{config.redTick} No results found.')

    @games_group.command(name='info', description='Check the status of the games search database')
    @app_commands.checks.cooldown(2, 60, key=lambda i: (i.guild_id, i.user.id))
    async def _games_info(self, interaction: discord.Interaction):
        '''Check search database status'''
        await interaction.response.defer()
        releases_url = f'https://www.giantbomb.com/games?game_filter[platform]={GIANTBOMB_NSW_ID}'
        embed = discord.Embed(
            title='Game Search Database Status',
            description=(
                'Our game search database is powered by the [GiantBomb API](https://www.giantbomb.com/api), filtered to'
                f' [Nintendo Switch releases]({releases_url}). Please contribute corrections of any data inaccuracies'
                f' to [their wiki](https://www.giantbomb.com/games).'
            ),
        )

        game_count = self.db.find({'_type': 'game'}).count()
        release_count = self.db.find({'_type': 'release'}).count()
        embed.add_field(name='Games Stored', value=game_count, inline=True)
        embed.add_field(name='Releases Stored', value=release_count, inline=True)

        for key, string in [('part', 'Partial'), ('full', 'Full')]:
            sync = self.last_sync[key]
            if sync['running']:
                value = 'In progress...'
            elif sync['at'] is None:
                value = 'Never ran'
            else:
                count = sync["count"]
                value = f'<t:{int(sync["at"].timestamp())}:R>: {count["games"]} games, {count["releases"]} releases'

            embed.add_field(name=f'Last {string} Sync', value=value, inline=False)

        return await interaction.followup.send(embed=embed)

    async def games_sync(self, interaction: discord.Interaction, full: bool):
        '''Force a database sync'''
        await interaction.response.send_message('Running sync...')
        try:
            c, detail = await self.sync_db(full)
            message = f'{config.greenTick} Finished syncing {c["games"]} games and {c["releases"]} releases {detail}'
            return await interaction.edit_original_response(content=message)

        except RatelimitException:
            return await interaction.edit_original_response(
                content=f'{config.redTick} Unable to complete sync, ratelimited'
            )


async def setup(bot):
    await bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


async def teardown(bot):
    await bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
