import asyncio
import logging
import re
from typing import Union

import config
import discord
from discord.ext import commands


class ChatRoleEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active = False
        self.role = None
        self.notify = None
        self.ids = {'text': [], 'cat': []}

    def embed(self, ctx):
        if self.ids['text']:
            channels = ' '.join([ctx.guild.get_channel(id).mention for id in self.ids['text']])
        else:
            channels = '*None*'

        if self.ids['cat']:
            categories = ' '.join([f'`{ctx.guild.get_channel(id)}`' for id in self.ids['cat']])
        else:
            categories = '*None*'

        return discord.Embed(
            description=(
                f'**Notify Users**: {self.notify}\n'
                f'**Role**: {ctx.guild.get_role(self.role).mention}\n'
                f'**Text Channel{"" if len(self.ids["text"]) == 1 else "s"}:** {channels}\n'
                f'**Categor{"y" if len(self.ids["cat"]) == 1 else "ies"}:** {categories}'
            ),
        )

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='chatrole', invoke_without_command=True)
    async def _chatrole(self, ctx):
        '''Manages a event where communicating in text channel(s) gives a special role.'''
        return await ctx.send_help(self._chatrole)

    @commands.has_any_role(config.moderator, config.eh)
    @_chatrole.command(name='start')
    async def _chatrole_start(
        self,
        ctx,
        notify_users: bool,
        role: discord.Role,
        channel_or_catagories: commands.Greedy[Union[discord.TextChannel, discord.CategoryChannel]],
    ):
        if self.active:
            return await ctx.send(f'{config.redTick} A chat role event is already running!')

        self.notify = notify_users
        self.role = role.id
        self.ids = {'text': [], 'cat': []}

        for channel in channel_or_catagories:
            if isinstance(channel, discord.channel.TextChannel):
                self.ids['text'].append(channel.id)
            elif isinstance(channel, discord.channel.CategoryChannel):
                self.ids['cat'].append(channel.id)
            else:
                return await ctx.send(f'{config.redTick} Invalid channel type: {channel}')

        self.active = True
        return await ctx.send(f'{config.greenTick} Started chat role event:', embed=self.embed(ctx))

    @commands.has_any_role(config.moderator, config.eh)
    @_chatrole.command(name='stop')
    async def _chatrole_stop(self, ctx):
        if self.active:
            self.active = False
            return await ctx.send(f'{config.greenTick} Ended chat role event:', embed=self.embed(ctx))
        else:
            return await ctx.send(f'{config.redTick} A chat role event is not currently running!')

    @commands.has_any_role(config.moderator, config.eh)
    @_chatrole.command(name='status')
    async def _chatrole_status(self, ctx):
        if self.active:
            return await ctx.send('A chat role event is currently running:', embed=self.embed(ctx))
        else:
            return await ctx.send('A chat role event is not currently running!')

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
        if not self.active or message.author.bot or isinstance(message.channel, discord.channel.DMChannel):
            return

        if (message.channel.id not in self.ids['text']) and (message.channel.category_id not in self.ids['cat']):
            return

        role = message.guild.get_role(self.role)

        if role not in message.author.roles:
            if self.notify:
                await message.reply(f'You have been given the **{str(role)}** role!', delete_after=10)

            returnthis


def setup(bot):
    bot.add_cog(ChatRoleEvent(bot))
    logging.info('[Extension] ChatRoleEvent module loaded')


def teardown(bot):
    bot.remove_cog('ChatRoleEvent')
    logging.info('[Extension] ChatRoleEvent module unloaded')
