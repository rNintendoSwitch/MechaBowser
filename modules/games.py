import calendar
import collections
import copy
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Literal, Optional, Tuple, Union

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

AUTO_SYNC = False
SEARCH_RATIO_THRESHOLD = 50


class DekuDeals:
    def __init__(self, api_key):
        self.ENDPOINT = 'https://www.dekudeals.com/api/rNS/games'
        self.api_key = api_key

    async def fetch_games(self, platform: str):
        offset = 0

        for _ in range(1, 5):  # TODO: change this to 1000 later or something
            async with aiohttp.ClientSession() as session:
                headers = {'User-Agent': 'MechaBowser (+https://github.com/rNintendoSwitch/MechaBowser)'}
                params = {'api_key': self.api_key, 'offset': offset}

                if platform:
                    params['platform'] = platform

                async with session.get(self.ENDPOINT, params=params, headers=headers) as resp:
                    resp_json = await resp.json()

                    for item in resp_json['games']:
                        yield item

                    offset += len(resp_json['games'])

                    if len(resp_json['games']) < 100:
                        break  # no more results expected


class Games(commands.Cog, name='Games'):
    def __init__(self, bot):
        self.bot = bot
        self.DekuDeals = DekuDeals(config.dekudeals)
        self.db = mclient.bowser.games

        self.last_sync = {'at': None, 'running': False}

        # Ensure indices exist
        self.db.create_index([("deku_id", pymongo.ASCENDING)], unique=True)

        # Generate the pipeline
        self.pipeline = [
            {'$project': {'deku_id': 1, 'name': 1, 'release_date': 1}},  # Filter to only stuff we want
        ]
        self.aggregatePipeline = list(self.db.aggregate(self.pipeline))

        if AUTO_SYNC:
            self.sync_db.start()

    async def cog_unload(self):
        if AUTO_SYNC:
            self.sync_db.cancel()

    @tasks.loop(hours=1)
    async def sync_db(self) -> Tuple[int, str]:
        logging.info(f'[Games] Syncing games database...')
        self.last_sync['running'] = True

        # Fields that are per release and will need some manipulation
        release_fields = ['_last_synced', 'eshop_price', 'release_date']

        # Generate a timestamp to use for marking last sync time
        sync_time = datetime.now(tz=timezone.utc)

        count = 0
        for platform in ['switch_1', 'switch_2']:
            try:
                async for game in self.DekuDeals.fetch_games(platform):
                    game['_last_synced'] = sync_time

                    if game['release_date']:
                        game['release_date'] = parser.parse(game['release_date'])

                    # Filter out fields unique to releases and upsert
                    filtered_update_dict = {k: v for k, v in game.items() if k not in release_fields}

                    # Readd the release fields in a platform subset
                    for field in release_fields:
                        if field not in filtered_update_dict:
                            filtered_update_dict[field] = dict()

                        filtered_update_dict[field][platform] = game[field] if field in game else None

                    self.db.update_one({'deku_id': game['deku_id']}, {'$set': filtered_update_dict}, upsert=True)
                    count += 1

            except Exception as e:
                logging.error(f'[Games] Exception while syncing games: {e}')
                raise

            # Remove data from release fields for any that didn't get updated for this release
            unset_dict = dict()
            for field in release_fields:
                if field not in unset_dict:
                    unset_dict[field] = dict()

                unset_dict[field][platform] = ''

            self.db.update_many({'_last_synced': {platform: {'$lt': sync_time}}}, {'$unset': unset_dict})

        # If items were not updated, delete them
        self.db.delete_many(
            {"$or": [{"_last_synced.switch_1": {'$lt': sync_time}}, {"_last_synced.switch_2": {'$lt': sync_time}}]}
        )

        logging.info(f'[Games] Finished syncing {count} games')

        self.last_sync = {'at': sync_time, 'running': False}
        self.aggregatePipeline = list(self.db.aggregate(self.pipeline))

        return count

    def search(self, query: str) -> Optional[dict]:
        match = {'deku_id': None, 'score': None, 'name': None}
        for game in self.aggregatePipeline:

            methods = [fuzz.ratio, fuzz.partial_ratio, fuzz.token_sort_ratio, fuzz.token_set_ratio]
            rem_punc = re.compile('[^0-9a-zA-Z ]+')

            # Remove punctuation and casing for name and query
            scores = [
                method(rem_punc.sub('', game['name'].lower()), rem_punc.sub('', query.lower())) for method in methods
            ]
            score = sum(scores) / len(methods)

            if not match['score'] or (score > match['score']):
                match = {'deku_id': game['deku_id'], 'score': score, 'name': game['name']}

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

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    class GamesCommand(app_commands.Group):
        pass

    games_group = GamesCommand(name='game', description='Find out information about games for the Nintendo Switch!')

    @games_group.command(name='search')
    @app_commands.describe(query='The term you want to search for a game')
    @app_commands.checks.cooldown(2, 60, key=lambda i: (i.guild_id, i.user.id))
    async def _games_search(self, interaction: discord.Interaction, query: str):
        '''Search for Nintendo Switch games'''
        await interaction.response.defer()
        game = self.search(query)

        if game:
            embed = discord.Embed(
                title=game['name'],
                description=game["deck"],
                url=f"https://web.archive.org/web/{game['site_detail_url']}",
                timestamp=game['date_last_updated'],
            )
            embed.set_author(
                name='Data via GiantBomb',
                url=f'https://www.giantbomb.com/api',
                icon_url='https://avatars.githubusercontent.com/u/214028297',
            )

            image = await self.get_image(result['guid'], 'small_url', as_url=True)
            if image:
                embed.set_thumbnail(url=image)

            embed.set_footer(text=f'{result["score"] }% confident ⯁ Entry last updated')

            # TODO: publishers/developers

            # Build release date line
            if self.parse_expected_release_date(game):
                game_desc = f'\n**Expected Release Date:** {self.parse_expected_release_date(game, True)}'
            elif game["original_release_date"]:
                game_desc = f'\n**Release Date:** {game["original_release_date"].strftime("%b. %d, %Y")}'
            else:
                game_desc = f'\n**Release Date:** *Unknown*'

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

                switch_desc = (
                    f'[**{release_count} known Nintendo Switch release{("" if release_count == 1 else "s")}**]'
                    f'(https://web.archive.org/web/{game["site_detail_url"]}releases)'
                )

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
        embed = discord.Embed(
            title='Game Search Database Status',
            description=(
                'Game search data provided by [Deku Deals](https://www.dekudeals.com/games?utm_campaign=rnintendoswitch'
                '&utm_medium=social&utm_source=discord&utm_content=mechabowser-game-status).'
            ),
        )

        game_count = self.db.count_documents({})
        embed.add_field(name='Games Stored', value=game_count, inline=True)

        if self.last_sync['running']:
            last_sync = f"*In-progress...*"
        elif self.last_sync['at']:
            last_sync = f"<t:{int(self.last_sync['at'].timestamp())}:R>"
        else:
            newest_update_game = self.db.find_one(sort=[("_last_synced.switch_1", -1)])
            last_sync = f"<t:{int(newest_update_game['_last_synced']['switch_1'].timestamp())}:R>"

        embed.add_field(name=f'Last Sync', value=last_sync, inline=True)

        return await interaction.followup.send(embed=embed)

    # called by core.py
    async def games_sync(self, interaction: discord.Interaction):
        '''Force a database sync'''
        await interaction.response.send_message('Running sync...')

        count = await self.sync_db()
        message = f'{config.greenTick} Finished syncing {count} games'
        return await interaction.edit_original_response(content=message)


async def setup(bot):
    await bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


async def teardown(bot):
    await bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
