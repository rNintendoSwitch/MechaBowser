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
DEKU_UTM = "utm_campaign=rnintendoswitch&utm_medium=social&utm_source=discord"


class DekuDeals:
    def __init__(self, api_key):
        self.ENDPOINT = 'https://www.dekudeals.com/api/rNS/games'
        self.api_key = api_key

    async def fetch_games(self, platform: str):
        offset = 0

        for _ in range(1, 1500):
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
                        filtered_update_dict[f"{field}.{platform}"] = game[field] if field in game else None

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

    async def get_image(self, deku_id: str, as_url: bool = False) -> Union[str, None]:
        game = self.db.find_one({'deku_id': deku_id}, projection={'image': 1})

        if not game or 'image' not in game or type not in game['image']:
            return None

        url = game['image']

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
        result = self.search(query)

        if result and result['deku_id']:
            game = self.db.find_one({'deku_id': result['deku_id']})

        if game:
            embed = discord.Embed(
                title=game['name'],
                url=f"{game['deku_link']}?{DEKU_UTM}&utm_content=mechabowser-game-search",
                timestamp=self.get_db_last_update(),
            )
            embed.set_footer(
                text=f'Data provided by DekuDeals ⯁ Last fetched',
                icon_url='https://www.dekudeals.com/favicon-32x32.png',
            )

            if game['image']:
                embed.set_thumbnail(url=game['image'])

            # Release Dates
            dates = {}

            if game["release_date"] and "switch_1" in game["release_date"] and game["release_date"]["switch_1"]:
                dates["Switch"] = game["release_date"]["switch_1"].date()
            elif 'switch_1' in game['_last_synced']:
                dates["Switch"] = "*Unknown*"

            if game["release_date"] and "switch_2" in game["release_date"] and game["release_date"]["switch_2"]:
                dates["Switch 2"] = game["release_date"]["switch_2"].date()
            elif 'switch_2' in game['_last_synced']:
                dates["Switch 2"] = "*Unknown*"

            lines = []
            for platform, date in dates.items():
                lines.append(f"{platform}: {date}")

            s = "" if len(dates) == 1 else "s"
            embed.add_field(name=f'Release Date{s}', value="\n".join(lines), inline=False)

            # eShop Prices
            if game["eshop_price"]:
                prices = {}

                if "switch_1" in game["eshop_price"] and game["eshop_price"]["switch_1"]:
                    if "us" in game["eshop_price"]["switch_1"] and game["eshop_price"]["switch_1"]["us"]:
                        prices["Switch"] = game["eshop_price"]["switch_1"]["us"]

                if "switch_2" in game["eshop_price"] and game["eshop_price"]["switch_2"]:
                    if "us" in game["eshop_price"]["switch_2"] and game["eshop_price"]["switch_2"]["us"]:
                        prices["Switch 2"] = game["eshop_price"]["switch_2"]["us"]

                lines = []
                for platform, price in prices.items():
                    msrp = price['msrp'] / 100 if price['msrp'] else None
                    curr = price['price'] / 100 if price['price'] else None

                    if curr and msrp and curr < msrp:
                        lines.append(f"{platform}: ~~${msrp:.2f}~~ ${curr:.2f}")
                    elif curr:
                        lines.append(f"{platform}: ${curr:.2f}")
                    else:
                        lines.append(f"{platform}: ${msrp:.2f}")

                if lines:
                    s = "" if len(prices) == 1 else "s"
                    embed.add_field(name=f'US eShop Price{s}', value="\n".join(lines), inline=False)

            # Devs and Pubs
            if game['developers']:
                s = "" if len(game['developers']) == 1 else "s"
                embed.add_field(name=f'Developer{s}', value=", ".join(game['developers']), inline=True)

            if game['publishers']:
                s = "" if len(game['publishers']) == 1 else "s"
                embed.add_field(name=f'Publisher{s}', value=", ".join(game['publishers']), inline=True)

            return await interaction.followup.send(embed=embed)

        else:
            return await interaction.followup.send(f'{config.redTick} No results found.')

    def get_db_last_update(self):
        if self.last_sync['at']:
            return self.last_sync['at']

        else:
            newest_sw1_update_game = self.db.find_one(sort=[("_last_synced.switch_1", -1)])
            newest_sw2_update_game = self.db.find_one(sort=[("_last_synced.switch_2", -1)])

            if newest_sw1_update_game['_last_synced']['switch_1'] > newest_sw2_update_game['_last_synced']['switch_2']:
                return newest_sw1_update_game['_last_synced']['switch_1']
            else:
                return newest_sw2_update_game['_last_synced']['switch_2']

    @games_group.command(name='info', description='Check the status of the games search database')
    @app_commands.checks.cooldown(2, 60, key=lambda i: (i.guild_id, i.user.id))
    async def _games_info(self, interaction: discord.Interaction):
        '''Check search database status'''
        await interaction.response.defer()
        embed = discord.Embed(
            title='Game Search Database Status',
            description=(
                'Game data provided by '
                f'[Deku Deals](https://www.dekudeals.com/games?{DEKU_UTM}&utm_content=mechabowser-game-status)'
            ),
        )

        game_count = self.db.count_documents({})
        embed.add_field(name='Games Stored', value=game_count, inline=True)

        if self.last_sync['running']:
            last_sync = f"*Fetch in-progress...*"
        else:
            last_sync = f"<t:{int(self.get_db_last_update().timestamp())}:R>"

        embed.add_field(name=f'Last Fetch', value=last_sync, inline=True)

        return await interaction.followup.send(embed=embed)

    # called by core.py
    async def games_sync(self, interaction: discord.Interaction):
        await interaction.response.send_message('Running fetch...')

        count = await self.sync_db()
        message = f'{config.greenTick} Finished fetching {count} games'
        return await interaction.edit_original_response(content=message)


async def setup(bot):
    await bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


async def teardown(bot):
    await bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
