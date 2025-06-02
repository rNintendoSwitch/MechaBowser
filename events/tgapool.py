import logging
from datetime import datetime

import aiohttp
import config
import discord
import pymongo
from discord import app_commands
from discord.ext import commands, tasks

from tools import commit_profile_change


mclient = pymongo.MongoClient(config.mongoURI)


class TGAPool(commands.Cog):
    def __init__(self, bot):
        self.GUILD = 238080556708003851
        self.EVENT_CHANNEL = 672550040979636244
        self.ENDPOINT = 'https://switchcord.net/api/rewards'
        self.BACKGROUND = 'the-game-awards-2023'
        self.TROPHIES_PREFIX = 'tga-'
        self.TROPHIES = ['tga-gold', 'tga-silver', 'tga-bronze']

        self.bot = bot
        self.db = mclient.bowser.users
        self.guild = self.bot.get_guild(self.GUILD)
        self.event_channel = self.guild.get_channel(self.EVENT_CHANNEL)

        self.response_check.start()

    @app_commands.command(name='tgagrant', description='Automatically assign trophies based on a user\'s predictions')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def tgatrophygrant(self, interaction):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            headers = {'User-Agent': 'MechaBowser (+https://github.com/rNintendoSwitch/MechaBowser)'}
            async with session.get(self.ENDPOINT, headers=headers) as resp:
                users = await resp.json()
                for user in users:
                    dbUser = self.db.find_one(int(user['id']))

                    if not user['earnedTrophy'] or not dbUser:
                        continue

                    trophy_name = f'{self.TROPHIES_PREFIX}{user["earnedTrophy"]}'

                    if trophy_name not in self.TROPHIES:
                        logging.error(f'[TGAPool] Invalid trophy {trophy_name} for {user["id"]}')
                        continue

                    if trophy_name not in dbUser['trophies']:
                        self.db.update_one({'_id': int(user['id'])}, {'$push': {'trophies': trophy_name}})

                        msg = f':information_source: Assigned TGA trophy `{trophy_name}` to <@{user["id"]}>'
                        await interaction.followup.send(msg, allowed_mentions=discord.AllowedMentions.none())

                        if interaction.channel.id != self.event_channel.id:
                            await self.event_channel.send(msg, allowed_mentions=discord.AllowedMentions.none())
                            await interaction.followup.send(
                                f'{config.greenTick} Done. Check {self.event_channel.mention} for output'
                            )

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
                        member = await self.bot.fetch_user(int(user['id']))
                        await commit_profile_change(self.bot, member, 'background', self.BACKGROUND)
                        await self.event_channel.send(
                            f':information_source: Assigned TGA background to <@{user["id"]}>',
                            allowed_mentions=discord.AllowedMentions.none(),
                        )

    async def cog_unload(self):
        self.response_check.cancel()  # pylint: disable=no-member


logging.info('')


async def setup(bot):
    await bot.add_cog(TGAPool(bot))
    logging.info('[Extension] TGAPool module loaded')


async def teardown(bot):
    await bot.remove_cog('TGAPool')
    logging.info('[Extension] TGAPool module unloaded')
