import logging
from datetime import datetime

import aiohttp
import config
import discord
import pymongo
from discord.ext import commands, tasks


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)


class TGAPool(commands.Cog):
    def __init__(self, bot):
        self.GUILD = 238080556708003851
        self.EVENT_CHANNEL = 654018662860193830
        self.ENDPOINT = 'https://switchcord.net/rewards'
        self.BACKGROUND = 'the-game-awards'
        self.TROPHIES_PREFIX = 'tga-'
        self.TROPHIES = ['tga-gold', 'tga-silver', 'tga-bronze']

        self.bot = bot
        self.db = mclient.bowser.users
        self.guild = self.bot.get_guild(self.GUILD)
        self.event_channel = self.guild.get_channel(self.EVENT_CHANNEL)

        self.response_check.start()

    @commands.command(name='tgatrophygrant')
    @commands.check_any(commands.is_owner(), commands.has_guild_permissions(administrator=True))
    async def tgatrophygrant(self, ctx):
        async with aiohttp.ClientSession() as session:
            headers = {'User-Agent': 'MechaBowser (+https://github.com/rNintendoSwitch/MechaBowser)'}
            async with session.get(self.ENDPOINT, headers=headers) as resp:
                users = await resp.json()
                for user in users:
                    dbUser = self.db.find_one(int(user['id']))

                    if not user['earnedTrophy'] or not dbUser:
                        logging.info('no trophy for u ' + user["id"])
                        continue

                    trophy_name = f'{self.TROPHIES_PREFIX}{user["earnedTrophy"]}'

                    if trophy_name not in self.TROPHIES:
                        logging.error(f'[TGAPool] Invalid trophy {trophy_name} for {user["id"]}')
                        continue

                    if trophy_name not in dbUser['trophies']:
                        self.db.update_one({'_id': int(user['id'])}, {'$push': {'trophies': trophy_name}})

                        msg = f':information_source: Assigned TGA trophy `{trophy_name}` to <@{user["id"]}>'
                        await ctx.reply(msg, allowed_mentions=discord.AllowedMentions.none())

                        if ctx.channel.id != self.bot.event_channel.id:
                            await self.event_channel.send(msg, allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(seconds=30)
    async def response_check(self):
        async with aiohttp.ClientSession() as session:
            headers = {'User-Agent': 'MechaBowser (+https://github.com/rNintendoSwitch/MechaBowser)'}
            async with session.get(self.ENDPOINT, headers=headers) as resp:
                users = await resp.json()
                for user in users:
                    dbUser = self.db.find_one(int(user['id']))

                    if not user['earnedBackground'] or not dbUser:
                        continue

                    if user['earnedBackground'] and self.BACKGROUND not in dbUser['backgrounds']:
                        self.db.update_one({'_id': int(user['id'])}, {'$push': {'backgrounds': self.BACKGROUND}})

                        await self.event_channel.send(
                            f':information_source: Assigned TGA background to <@{user["id"]}>',
                            allowed_mentions=discord.AllowedMentions.none(),
                        )

    def cog_unload(self):
        self.response_check.cancel()  # pylint: disable=no-member


def setup(bot):
    bot.add_cog(TGAPool(bot))
    logging.info('[Extension] TGAPool module loaded')


def teardown(bot):
    bot.remove_cog('TGAPool')
    logging.info('[Extension] TGAPool module unloaded')
