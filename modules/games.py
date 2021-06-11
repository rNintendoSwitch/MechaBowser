import collections
import datetime
import logging
from typing import Dict, Generator, Literal, Optional, Tuple

import aiohttp
import config  # type: ignore
import discord
import pymongo
import token_bucket
from dateutil import parser
from discord.ext import commands, tasks
from fuzzywuzzy import fuzz

import tools  # type: ignore


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)

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
        self, path: Literal['games', 'releases'], after: datetime.datetime = None
    ) -> Generator[dict, None, None]:
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
                    after = after + datetime.timedelta(0, 1)  # Add 1 sec
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

    # async def fetch_item(self, path: Literal['game', 'release'], guid: str) -> Optional[dict]:
    #     if path not in ['game', 'release']:
    #         raise ValueError(f'invalid path: {path}')

    #     async with aiohttp.ClientSession() as session:
    #         self.raise_for_ratelimit()

    #         params = {'api_key': self.api_key, 'format': 'json'}
    #         async with session.get(f'{self.BASE_URL}/{path}/{guid}', params=params) as resp:
    #             resp.raise_for_status()
    #             resp_json = await resp.json()

    #             return resp_json['results'] if resp_json['results'] else None


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

        if AUTO_SYNC:
            self.sync_db.start()  # pylint: disable=no-member

    def cog_unload(self):
        if AUTO_SYNC:
            self.sync_db.cancel()  # pylint: disable=no-member

    @tasks.loop(hours=1)
    async def sync_db(self, force_full: bool = False) -> Tuple[int, str]:
        # If last full sync was more then a day ago (or on restart/forced), preform a new full sync
        day_ago = datetime.datetime.utcnow() - datetime.timedelta(days=1)
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
            async for game in self.GiantBomb.fetch_items(path, None if full else after):
                if full:
                    game['_full_sync_updated'] = True

                self.update_item_in_db(type, game)
                count[path] += 1

        if full:
            self.db.delete_many({'_full_sync_updated': False})  # If items were not updated, delete them

        logging.info(f'[Games] Finished syncing {count["games"]} games and {count["releases"]} releases {detail_str}')
        self.last_sync['full' if full else 'part'] = {
            'at': datetime.datetime.utcnow(),
            'count': count,
            'running': False,
        }

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

    def search(self, query: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        SCORE = 1
        match = (None, None, None)

        pipeline = [
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

        for game in self.db.aggregate(pipeline):
            names = collections.Counter([game['name']])
            if game['aliases']:
                names.update(game['aliases'])
            if game['_releases']:
                names.update([release['name'] for release in game['_releases']])

                # Ignore this game if it has no release dates (if has releases), or release date if no releases
                if all(release['release_date'] is None for release in game['_releases']):
                    continue

            else:  # no releases
                if game['original_release_date'] is None:
                    continue

            for name in names:
                methods = [fuzz.ratio, fuzz.partial_ratio, fuzz.token_sort_ratio, fuzz.token_set_ratio]

                scores = [method(name.lower(), query.lower()) for method in methods]
                score = sum(scores) / len(methods)

                if not match[SCORE] or (score > match[SCORE]):
                    match = (game['guid'], score, name)

        if match[SCORE] < SEARCH_RATIO_THRESHOLD:
            return (None, None, None)

        return match

    def get_preferred_name(self, guid: str) -> Optional[str]:
        game = self.db.find_one({'_type': 'game', 'guid': guid}, projection={'name': 1, 'id': 1})
        if not game:
            return None

        releases_cursor = self.db.find({'_type': 'release', 'game.id': game['id']}, projection={'name': 1})
        release_names = [release['name'] for release in list(releases_cursor)] if releases_cursor else []

        # If all releases share a common root, use the common name of the releases, otherwise use the game name
        if release_names:
            # Get common starting part of releases name
            # https://code.activestate.com/recipes/252177-find-the-common-beginning-in-a-list-of-strings/#c10
            names = [r.lower() for r in release_names]
            common_start = names[0][: ([min([x[0] == elem for elem in x]) for x in zip(*names)] + [0]).index(0)]

            if len(common_start) >= 8:
                return release_names[0][: len(common_start)]  # Access the name of a release to preserve sane casing

        return game['name']

    @commands.group(name='games', aliases=['game'], invoke_without_command=True)
    async def _games(self, ctx):
        '''Search for games or check search database status'''
        return await ctx.send_help(self._games)

    @_games.command(name='search')
    async def _games_search(self, ctx, *, query: str):
        '''Search for Nintendo Switch games'''
        guid, score, name = self.search(query)
        await ctx.reply(f'{guid}@{score}: {name} / Preferred: {self.get_preferred_name(guid)}')
        # result, score, alias = self.search(query)

        # if result:
        #     detail = await self.fetch_game_detail(result['guid'])  # type: ignore

        # if result is None or detail is None:
        #     return await ctx.send(f'{config.redTick} No results found!')

        # name = result["name"]
        # aliases = f' *({alias})*' if alias else ''
        # url = result["site_detail_url"]  # type: ignore

        # return await ctx.reply(f'**{name}**{aliases} - {score}\n{url}')

    @_games.command(name='info', aliases=['information'])
    async def _games_info(self, ctx):
        '''Check search database status'''
        embed = discord.Embed(
            title='Game Search Database Status',
            description=(
                'Our game search database is powered by the [GiantBomb API](https://www.giantbomb.com/api), filtered to'
                ' [Nintendo Switch releases](https://www.giantbomb.com/games?game_filter[platform]={GIANTBOMB_NSW_ID}).'
                ' Please contribute corrections of any data inaccuracies to their wiki.'
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
                value = f'{tools.humanize_duration(sync["at"])}: {count["games"]} games, {count["releases"]} releases'

            embed.add_field(name=f'Last {string} Sync', value=value, inline=False)

        return await ctx.send(embed=embed)

    @_games.command(name='sync', aliases=['synchronize'])
    @commands.is_owner()
    async def _games_sync(self, ctx, full: bool = False):
        '''Force a database sync'''
        await ctx.reply('Running sync...')
        count, detail_str = await self.sync_db(full)
        return await ctx.reply(f'Finished syncing {count["games"]} games and {count["releases"]} releases {detail_str}')

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if not ctx.command:
            return

        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(
                f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(
                f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command', delete_after=15)

        else:
            await ctx.send(
                f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.',
                delete_after=15,
            )
            raise error


def setup(bot):
    bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


def teardown(bot):
    bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
