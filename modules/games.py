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
        self.db.create_index([("name", pymongo.ASCENDING), ("aliases", pymongo.ASCENDING)])
        self.db.create_index([("date_last_updated", pymongo.DESCENDING)])
        self.db.create_index([("guid", pymongo.ASCENDING)], unique=True)
        self.db.create_index([("type", pymongo.ASCENDING)])

        if AUTO_SYNC:
            self.sync_db.start()  # pylint: disable=no-member

    def cog_unload(self):
        if AUTO_SYNC:
            self.sync_db.cancel()  # pylint: disable=no-member

    @tasks.loop(hours=1)
    async def sync_db(self, force_full: bool = False) -> Dict[str, int]:
        # If last full sync was more then a day ago (or on restart/forced), preform a new full sync
        day_ago = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        full = force_full or ((self.last_sync['full']['at'] < day_ago) if self.last_sync['full']['at'] else True)

        if not full:
            try:
                latest_doc = self.db.find().sort("date_last_updated", pymongo.DESCENDING).limit(1).next()
                after = latest_doc['date_last_updated']
            except StopIteration:
                full = True  # Do full sync if we're having issues getting latest updated

        logging.info('[Games] Syncing games database ' + ('(full)...' if full else f'(partial after {after})...'))
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

        logging.info(f'[Games] Finished syncing {count["games"]} games and {count["releases"]} releases')
        self.last_sync['full' if full else 'part'] = {
            'at': datetime.datetime.utcnow(),
            'count': count,
            'running': False,
        }

        return count

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

        game['type'] = type

        return self.db.replace_one({'id': game['id']}, game, upsert=True)

    # def search(self, query: str) -> Tuple[Optional[dict], Optional[int], Optional[str]]:
    #     match_ratio = 0
    #     match_game = None
    #     match_name = None

    #     pipeline = [
    #         # original_release_date - This only really works for games not released on other platforms
    #         {'$match': {'original_release_date': {'$ne': None}}},
    #         {'$project': {'name': 1, 'aliases': 1}},
    #     ]

    #     for game in self.db.aggregate(pipeline):
    #         names = [game['name']]
    #         if game['aliases']:
    #             names += game['aliases']

    #         for name in names:
    #             methods = [fuzz.ratio, fuzz.partial_ratio, fuzz.token_sort_ratio, fuzz.token_set_ratio]

    #             scores = [method(name.lower(), query.lower()) for method in methods]
    #             ratio = sum(scores) / len(methods)

    #             if ratio > match_ratio:
    #                 match_ratio = ratio
    #                 match_game = game
    #                 match_name = name

    #     if not match_game or match_ratio < SEARCH_RATIO_THRESHOLD:
    #         return (None, None, None)

    #     document = self.db.find({'_id': match_game['_id']})
    #     alias = match_name if match_name in (match_game['aliases'] or []) else None
    #     return (document.next(), match_ratio, alias)

    @commands.group(name='games', aliases=['game'], invoke_without_command=True)
    async def _games(self, ctx):
        '''Search for games or check search database status'''
        return await ctx.send_help(self._games)

    # @_games.command(name='search')
    # async def _games_search(self, ctx, *, query: str):
    #     '''Search for Nintendo Switch games'''
    #     result, score, alias = self.search(query)

    #     if result:
    #         detail = await self.fetch_game_detail(result['guid'])  # type: ignore

    #     if result is None or detail is None:
    #         return await ctx.send(f'{config.redTick} No results found!')

    #     name = result["name"]
    #     aliases = f' *({alias})*' if alias else ''
    #     url = result["site_detail_url"]  # type: ignore

    #     return await ctx.reply(f'**{name}**{aliases} - {score}\n{url}')

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

        game_count = self.db.find({'type': 'game'}).count()
        release_count = self.db.find({'type': 'release'}).count()
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
        count = await self.sync_db(full)
        return await ctx.reply(f'Finished syncing {count["games"]} games and {count["releases"]} releases')

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
