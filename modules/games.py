import aiohttp
from typing import Generator

import config
import datetime
import logging
import token_bucket
import pymongo

from discord.ext import commands

mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)

GIANTBOMB_NSW_ID = 157


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
                params = {
                    'api_key': self.api_key,
                    'format': 'json',
                    'limit': 100,
                    'offset': offset,
                    'platforms': GIANTBOMB_NSW_ID,
                    'sort': 'date_last_updated:asc',
                }

                if after:
                    params['filter'] = f'date_last_updated:{after.isoformat(" ", timespec="seconds")}'

                self.raise_for_ratelimit()

                async with session.get(f'{self.BASE_URL}/games', params=params) as resp:
                    resp.raise_for_status()
                    resp_json = await resp.json()

                    for game in resp_json['results']:
                        yield game

                    offset += resp_json['number_of_page_results']
                    if offset > resp_json['number_of_total_results']:
                        break  # no more results


class Games(commands.Cog, name='Games'):
    def __init__(self, bot):
        self.GiantBomb = GiantBomb(config.giantbomb)


def setup(bot):
    bot.add_cog(Games(bot))
    logging.info('[Extension] Games module loaded')


def teardown(bot):
    bot.remove_cog('Games')
    logging.info('[Extension] Games module unloaded')
