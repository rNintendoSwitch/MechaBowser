import io
import logging
from datetime import datetime, timezone
from typing import Tuple

import aiohttp
import config  # type: ignore
import discord
import pymongo
import rapidfuzz
from dateutil import parser
from discord import app_commands
from discord.ext import commands, tasks


mclient = pymongo.MongoClient(config.mongoURI)

AUTO_SYNC = True
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

        self.gameNamesCache = None
        self.topGames = None
        self.recalculate_cache()

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
        for platform in ['switch', 'switch_2']:
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
            {"$or": [{"_last_synced.switch": {'$lt': sync_time}}, {"_last_synced.switch_2": {'$lt': sync_time}}]}
        )

        logging.info(f'[Games] Finished syncing {count} games')

        self.last_sync = {'at': sync_time, 'running': False}
        self.recalculate_cache()
        return count

    def recalculate_cache(self):
        self.gameNamesCache = list(self.db.aggregate([{'$project': {'deku_id': 1, 'name': 1}}]))

        # Cache the 10 most popular games
        users = mclient.bowser.users.find({"favgames": {'$exists': True, '$not': {'$size': 0}}})
        games = {}
        for user in users:
            for game_id in user['favgames']:
                if game_id not in games:
                    games[game_id] = 1

                else:
                    games[game_id] += 1

        top_10 = dict(sorted(games.items(), key=lambda kv: kv[1], reverse=True)[0:10])
        games = self.db.find({"deku_id": {"$in": list(top_10.keys())}}, projection={'deku_id': 1, 'name': 1})

        self.topGames = list(games)

    async def get_image(self, deku_id: str, as_url: bool = False):
        game = self.db.find_one({'deku_id': deku_id}, projection={'image': 1})

        if not game or 'image' not in game:
            return None

        url = game['image']

        if as_url:
            return url

        async with aiohttp.ClientSession() as session:
            headers = {'User-Agent': 'MechaBowser (+https://github.com/rNintendoSwitch/MechaBowser)'}
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.read()
                return io.BytesIO(data)

    def get_name(self, deku_id: str):
        document = self.db.find_one({'deku_id': deku_id}, projection={'name': 1})
        return document['name'] if document else None

    def search(self, query: str, multiResult=False):
        gameList = self.gameNamesCache

        # If our query isn't short (>5 chars), then filter out short game titles.
        # This prevents things like 'a' being the best match for 'realMyst' and not 'realMyst: Masterpiece Edition'
        if len(query) > 5:
            gameList = [g for g in gameList if len(g['name']) > 5]

        results = rapidfuzz.process.extract(
            query,
            [g['name'] for g in gameList],
            scorer=rapidfuzz.fuzz.WRatio,
            limit=10 if multiResult else 1,
            processor=rapidfuzz.utils.default_process,
            score_cutoff=SEARCH_RATIO_THRESHOLD,
        )

        if not results:
            return None

        ret = [{'deku_id': gameList[i]['deku_id'], 'score': score, 'name': name} for name, score, i in results]
        return ret if multiResult else ret[0]

    async def _games_search_autocomplete(self, interaction: discord.Interaction, current: str):
        if current:
            games = self.search(current, True)

        else:
            # Current textbox is empty
            return [app_commands.Choice(name=game['name'], value=game['deku_id']) for game in self.topGames]

        if games:
            return [app_commands.Choice(name=game['name'], value=game['deku_id']) for game in games]
        else:
            return []

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    class GamesCommand(app_commands.Group):
        pass

    games_group = GamesCommand(name='game', description='Find out information about games for the Nintendo Switch!')

    @games_group.command(name='search')
    @app_commands.describe(query='The term you want to search for a game')
    @app_commands.checks.cooldown(2, 60, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.autocomplete(query=_games_search_autocomplete)
    async def _games_search(self, interaction: discord.Interaction, query: str):
        '''Search for Nintendo Switch games'''
        await interaction.response.defer()

        user_deku_id = self.db.find_one({'deku_id': query.strip()})
        game = None

        if user_deku_id:
            game = user_deku_id  # User clicked an autocomplete, giving us the exact deku_id
            result = {'deku_id': user_deku_id['deku_id'], 'score': 100.0, 'name': user_deku_id['name']}

        else:
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
                text=f'Data provided by DekuDeals',
                icon_url='https://www.dekudeals.com/favicon-32x32.png',
            )

            if game['image']:
                embed.set_thumbnail(url=game['image'])

            # Release Dates
            dates = {}

            if game["release_date"] and "switch" in game["release_date"] and game["release_date"]["switch"]:
                dates["Switch"] = game["release_date"]["switch"].date()
            elif 'switch' in game['_last_synced']:
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

                if "switch" in game["eshop_price"] and game["eshop_price"]["switch"]:
                    if "us" in game["eshop_price"]["switch"] and game["eshop_price"]["switch"]["us"]:
                        prices["Switch"] = game["eshop_price"]["switch"]["us"]

                if "switch_2" in game["eshop_price"] and game["eshop_price"]["switch_2"]:
                    if "us" in game["eshop_price"]["switch_2"] and game["eshop_price"]["switch_2"]["us"]:
                        prices["Switch 2"] = game["eshop_price"]["switch_2"]["us"]

                lines = []
                for platform, price in prices.items():
                    msrp = price['msrp'] / 100 if price['msrp'] is not None else None
                    curr = price['price'] / 100 if price['price'] is not None else None

                    if curr is not None and msrp and curr < msrp:
                        if curr != 0:
                            discount = (1 - curr / msrp) * 100
                            lines.append(f"{platform}: ~~${msrp:.2f}~~ ${curr:.2f} *(-{discount:.0f}%)*")
                        else:
                            lines.append(f"{platform}: ~~${msrp:.2f}~~ Free *(-100%)*")
                    elif curr == 0:
                        lines.append(f"{platform}: Free")
                    elif curr:
                        lines.append(f"{platform}: ${curr:.2f}")
                    elif msrp:
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
            newest_sw1_update_game = self.db.find_one(sort=[("_last_synced.switch", -1)])
            newest_sw2_update_game = self.db.find_one(sort=[("_last_synced.switch_2", -1)])

            if newest_sw1_update_game['_last_synced']['switch'] > newest_sw2_update_game['_last_synced']['switch_2']:
                return newest_sw1_update_game['_last_synced']['switch']
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
