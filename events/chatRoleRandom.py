import asyncio
import logging
import random
import re
import typing
from typing import Union

import config
import discord
from discord.ext import commands


class ChatRoleRandomEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.roles = []

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='chatrolerandom', invoke_without_command=True)
    async def _chatrolerand(self, ctx, roles: commands.Greedy[discord.Role] = None):
        '''Manages a event where communicating in any text channel(s) gives a random role from a set.'''
        if roles:
            self.roles = [role.id for role in roles]
            return await ctx.message.reply(
                f'Event roles set: {" ".join([role.mention for role in roles])} ',
                allowed_mentions=discord.AllowedMentions.none(),
            )

        roleList = " ".join([str(role) for role in self.roles]) if self.roles else '<role ids>'
        enable = 'reenable' if self.roles else 'enable'
        self.roles = []

        return await ctx.message.reply(f'Event disabled. Run `{ctx.prefix}chatrolerandom {roleList}` to {enable}')

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if not ctx.command:
            return

        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        ctx.command
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
            return await ctx.send(f'{config.redTick} You do not have permission to run this command.', delete_after=15)

        else:
            await ctx.send(
                f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.',
                delete_after=15,
            )
            raise error

    @commands.Cog.listener()
    async def on_message(self, message):
        if not self.roles or message.author.bot or isinstance(message.channel, discord.channel.DMChannel):
            return

        member_role_ids = [role.id for role in message.author.roles]
        if not any([(role_id in member_role_ids) for role_id in self.roles]):

            role = message.guild.get_role(random.choice(self.roles))
            await message.author.add_roles(role)
            return await message.add_reaction('🏷️')


def setup(bot):
    bot.add_cog(ChatRoleRandomEvent(bot))
    logging.info('[Extension] ChatRoleRandomEvent module loaded')


def teardown(bot):
    bot.remove_cog('ChatRoleRandomEvent')
    logging.info('[Extension] ChatRoleRandomEvent module unloaded')
