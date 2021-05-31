import datetime
import logging
from typing import Generator

import aiohttp
import config
import discord
import pymongo
import token_bucket
from dateutil import parser
from discord.ext import commands, tasks
from fuzzywuzzy import fuzz

import tools


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)

GIANTBOMB_NSW_ID = 157
AUTO_SYNC = True


class RatelimitException(Exception):
    pass


class GiantBomb:
    def __init__(self, api_key):
        self.BASE_URL = 'https://www.giantbomb.com/api'
        self.api_key = api_key

        # Ratelimit burst limit 200, renews at 200 / 1hr
        self.bucket_storage = token_bucket.MemoryStorage()
        self.ratelimit = token_bucket.Limiter(200 / (60 * 60), 200, self.bucket_storage)

    def raise_for_ratelimit(self):
        rate_limited = not self.ratelimit.consume('global')
        if rate_limited:
            raise RatelimitException()

    async def fetch_games(self, after: datetime.datetime = None) -> Generator[dict, None, None]:
        offset = 0

        for _ in range(1, 1000):
            async with aiohttp.ClientSession() as session:
                self.raise_for_ratelimit()

                params = {
                    'api_key': self.api_key,
                    'format': 'json',
                    'limit': 100,
                    'offset': offset,
                    'sort': 'date_last_updated:asc',
                }

                if after:
                    after = after + datetime.timedelta(0, 1)  # Add 1 sec
                    start = after.isoformat(" ", timespec="seconds")
                    end = "2100-01-01 00:00:00"

                    # There is a bug in the GiantBomb API where if we want to fliter a platform and want to use another
                    # filter, we must place the platform in the filter key instead of using the platforms key.
                    # https://www.giantbomb.com/forums/api-developers-3017/unable-to-filter-games-by-date-added-1794952/#js-message-8288158
                    params['filter'] = f'date_last_updated:{start}|{end},platforms:{GIANTBOMB_NSW_ID}'
                else:
                    params['platforms'] = GIANTBOMB_NSW_ID

                async with session.get(f'{self.BASE_URL}/games', params=params) as resp:
                    resp.raise_for_status()
                    resp_json = await resp.json()

                    for game in resp_json['results']:
                        yield game

                    offset += resp_json['number_of_page_results']
                    if offset >= resp_json['number_of_total_results']:
                        break  # no more results

    # TODO: I think a game might actually get stuck in the database if the NSW platform is removed from it, so we might
    # either have to requery games manually, or re-do the entire database hmm

    # async def fetch_game(self, guid: str) -> dict:
    #     async with aiohttp.ClientSession() as session:
    #         self.raise_for_ratelimit()

    #         params = {'api_key': self.api_key, 'format': 'json'}
    #         async with session.get(f'{self.BASE_URL}/game/{guid}', params=params) as resp:
    #             resp.raise_for_status()
    #             resp_json = await resp.json()

    #             return resp_json['results']


class Games(commands.Cog, name='Games'):
    def __init__(self, bot):
        self.bot = bot
        self.GiantBomb = GiantBomb(config.giantbomb)
        self.db = mclient.bowser.games

        self.last_sync = {
            'part': {'at': None, 'count': 0, 'running': False},
            'full': {'at': None, 'count': 0, 'running': False},
        }

        # Ensure indices exist
        self.db.create_index([("name", pymongo.ASCENDING), ("aliases", pymongo.ASCENDING)])
        self.db.create_index([("date_last_updated", pymongo.DESCENDING)])
        self.db.create_index([("guid", pymongo.ASCENDING)], unique=True)

        if AUTO_SYNC:
            self.sync_games.start()  # pylint: disable=no-member

    def cog_unload(self):
        if AUTO_SYNC:
            self.sync_games.cancel()  # pylint: disable=no-member

    @tasks.loop(hours=1)
    async def sync_games(self, force_full=False):
        # If last full sync was more then a week ago (or on restart/forced), preform a new full sync
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

        count = 0
        async for game in self.GiantBomb.fetch_games(None if full else after):
            for key in ['date_added', 'date_last_updated', 'original_release_date']:  # Parse dates
                if game[key]:
                    game[key] = parser.parse(game[key])

            if game['aliases']:
                game['aliases'] = game['aliases'].splitlines()

            if full:
                game['_full_sync_updated'] = True

            self.db.replace_one({'id': game['id']}, game, upsert=True)
            count += 1

        if full:
            self.db.delete_many({'_full_sync_updated': False})  # If items were not updated, delete them

        logging.info(f'[Games] Finished syncing {count} games')
        self.last_sync['full' if full else 'part'] = {
            'at': datetime.datetime.utcnow(),
            'count': count,
            'running': False,
        }

        return count

    def search(self, query: str):
        match_ratio = 0
        match_game = None
        match_name = None

        pipeline = [
            # original_release_date - This only really works for games not released on other platforms
            {'$match': {'original_release_date': {'$ne': None}}},
            {'$project': {'name': 1, 'aliases': 1}},
        ]

        for game in self.db.aggregate(pipeline):
            names = [game['name']]
            if game['aliases']:
                names += game['aliases']

            for name in names:
                methods = [fuzz.ratio, fuzz.partial_ratio, fuzz.token_sort_ratio, fuzz.token_set_ratio]

                scores = [method(name.lower(), query.lower()) for method in methods]
                ratio = sum(scores) / len(methods)

                if ratio > match_ratio:
                    match_ratio = ratio
                    match_game = game
                    match_name = name

        if not match_game:
            return None

        document = self.db.find({'_id': match_game['_id']})
        alias = match_name if match_name in (match_game['aliases'] or []) else None
        return (document.next(), match_ratio, alias)

    @commands.group(name='games', aliases=['game'], invoke_without_command=True)
    async def _games(self, ctx):
        '''Search for games or check search database status'''
        return await ctx.send_help(self._games)

    @_games.command(name='search')
    async def _games_search(self, ctx, *, query: str):
        '''TODO Search for Nintendo Switch games'''
        result, score, alias = self.search(query)

        name = result["name"]
        aliases = f' *({alias})*' if alias else ''
        url = result["site_detail_url"]

        return await ctx.reply(f'**{name}**{aliases} - {score}\n{url}')

    @_games.command(name='info', aliases=['information'])
    async def _games_info(self, ctx):
        '''Check search database status'''
        embed = discord.Embed(
            title='Game Search Database Status',
            description=(
                'Our game search database is powered by the [GiantBomb API](https://www.giantbomb.com/api/), '
                'filtered to [Nintendo Switch releases]'
                f'(https://www.giantbomb.com/games/?game_filter[platform]={GIANTBOMB_NSW_ID}) Please feel free to '
                'contribute corrections to any inaccuracies to their wiki.'
            ),
        )

        count = self.db.find({}).count()
        embed.add_field(name='Games Stored', value=count, inline=False)

        for key, string in [('part', 'Partial'), ('full', 'Full')]:
            sync = self.last_sync[key]
            if sync['running']:
                value = 'In progress...'
            elif sync['at'] is None:
                value = 'Never ran'
            else:
                value = f'{tools.humanize_duration(sync["at"])}: {sync["count"]} games'

            embed.add_field(name=f'Last {string} Sync', value=value, inline=True)

        return await ctx.send(embed=embed)

    @_games.command(name='sync', aliases=['synchronize'])
    @commands.is_owner()
    async def _games_sync(self, ctx, full: bool = False):
        '''Force a database sync'''
        await ctx.reply('Running sync...')
        count = await self.sync_games(full)
        return await ctx.reply(f'Finished syncing {count} games')

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
