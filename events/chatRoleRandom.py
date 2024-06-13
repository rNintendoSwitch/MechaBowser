import asyncio
import logging
import random
import re
import typing
from typing import Union

import config
import discord
from discord import app_commands
from discord.ext import commands


class ChatRoleRandomEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.roles = []

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class ChatRoleRandomCommand(app_commands.Group):
        pass

    chatrolerand_group = ChatRoleRandomCommand(name='chatrolerandom', description='A whole server event that can distribute a list of roles randomly to users who send messages')

    @chatrolerand_group.command(name='start', description='Start an random user role event')
    @app_commands.describe(roles='A list of role IDs to randomly distribute')
    async def _chatrolerand_start(self, interaction: discord.Interaction, roles: str):
        '''Manages a event where communicating in any text channel(s) gives a random role from a set.'''
        roleList = []
        for role in roles.split():
            try:
                r = interaction.guild.get_role(int(role))
                if not r:
                    return interaction.response.send_message(f'{config.redTick} Invalid role `{role}` provided, please resolve and try again')

                self.roles.append(r.id)

            except:
                return interaction.response.send_message(f'{config.redTick} Invalid role `{role}` provided, please resolve and try again')

        return await interaction.response.send_message(
            f'Event roles set: {" ".join([role.mention for role in roles])} ',
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @chatrolerand_group.command(name='end', description='A list of role IDs to randomly distribute')
    async def _chatrolerand_end(self, interaction: discord.Interaction):
        roleList = " ".join([str(role) for role in self.roles]) if self.roles else '<role ids>'
        self.roles = []

        return await interaction.response.send_message(f'Event disabled. Run `/chatrolerandom start {roleList}` to begin another event')

    @commands.Cog.listener()
    async def on_message(self, message):
        if not self.roles or message.author.bot or isinstance(message.channel, discord.channel.DMChannel):
            return

        member_role_ids = [role.id for role in message.author.roles]
        if not any([(role_id in member_role_ids) for role_id in self.roles]):
            role = message.guild.get_role(random.choice(self.roles))
            await message.author.add_roles(role)
            return await message.add_reaction('🏷️')


async def setup(bot):
    await bot.add_cog(ChatRoleRandomEvent(bot))
    logging.info('[Extension] ChatRoleRandomEvent module loaded')


async def teardown(bot):
    await bot.remove_cog('ChatRoleRandomEvent')
    logging.info('[Extension] ChatRoleRandomEvent module unloaded')
