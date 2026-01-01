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


class Games(commands.Cog, name='Games'):
    def __init__(self, bot):
        self.bot = bot
        self.db = mclient.bowser.games

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

            # Build footer; if an match was an alias/release name, add it to footer
            has_alias = (result['name'] != name) and (result['name'] != game['name'])
            alias_str = (' ("' + result['name'] + '")') if has_alias else ''

            embed.set_footer(text=f'{result["score"] }% confident{alias_str} ⯁ Entry last updated')

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
                'Our game search database is powered by the [GiantBomb API](https://www.giantbomb.com/api), filtered to'
                ' Nintendo Switch releases.'
            ),
        )

        game_count = self.db.count_documents({'_type': 'game'})
        release_count = self.db.count_documents({'_type': 'release'})
        embed.add_field(name='Games Stored', value=game_count, inline=True)
        embed.add_field(name='Releases Stored', value=release_count, inline=True)

        newest_update_game = self.db.find_one(sort=[("date_last_updated", -1)])
        last_sync = int(newest_update_game['date_last_updated'].timestamp())

        embed.add_field(name=f'Last Sync', value=f'<t:{last_sync}:R>', inline=False)

        return await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


async def teardown(bot):
    await bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
