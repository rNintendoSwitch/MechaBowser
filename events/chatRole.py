import asyncio
import logging
import re
import typing

import config
import discord
from discord import app_commands
from discord.ext import commands


class ChatRoleEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active = False
        self.role = None
        self.notify = None
        self.ids = {'text': [], 'cat': []}

    def embed(self, interaction):
        if self.ids['text']:
            channels = ' '.join([interaction.guild.get_channel(id).mention for id in self.ids['text']])
        else:
            channels = '*None*'

        if self.ids['cat']:
            categories = ' '.join([f'`{interaction.guild.get_channel(id)}`' for id in self.ids['cat']])
        else:
            categories = '*None*'

        return discord.Embed(
            description=(
                f'**Notify Users**: {self.notify}\n'
                f'**Role**: {interaction.guild.get_role(self.role).mention}\n'
                f'**Text Channel{"" if len(self.ids["text"]) == 1 else "s"}:** {channels}\n'
                f'**Categor{"y" if len(self.ids["cat"]) == 1 else "ies"}:** {categories}'
            ),
        )

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class ChatRoleCommand(app_commands.Group):
        pass

    chatrole_group = ChatRoleCommand(
        name='chatrole',
        description='An event that automatically grants roles to users who participate in a channel or category',
    )

    @chatrole_group.command(name='start', description='Start a new chat role event')
    async def _chatrole_start(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        channel_or_catagory: typing.Union[discord.TextChannel, discord.CategoryChannel],
        notify_users: bool,
    ):
        if self.active:
            return await interaction.response.send_message(f'{config.redTick} A chat role event is already running!')

        self.notify = notify_users
        self.role = role.id
        self.ids = {'text': [], 'cat': []}

        if isinstance(channel_or_catagory, discord.channel.TextChannel):
            self.ids['text'].append(channel_or_catagory.id)
        elif isinstance(channel_or_catagory, discord.channel.CategoryChannel):
            self.ids['cat'].append(channel_or_catagory.id)
        else:
            return await interaction.response.send_message(f'{config.redTick} Invalid channel type: {channel}')

        self.active = True
        return await interaction.response.send_message(
            f'{config.greenTick} Started chat role event:', embed=self.embed(interaction)
        )

    @chatrole_group.command(name='stop', description='End an active role event')
    async def _chatrole_stop(self, interaction):
        if self.active:
            self.active = False
            return await interaction.response.send_message(
                f'{config.greenTick} Ended chat role event:', embed=self.embed(interaction)
            )
        else:
            return await interaction.response.send_message(
                f'{config.redTick} A chat role event is not currently running!'
            )

    @chatrole_group.command(
        name='status', description='Get the current status of the Chat Role module and any event currently running'
    )
    async def _chatrole_status(self, interaction):
        if self.active:
            return await interaction.response.send_message(
                'A chat role event is currently running:', embed=self.embed(interaction)
            )
        else:
            return await interaction.response.send_message('A chat role event is not currently running!')

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

            return await message.author.add_roles(role)


async def setup(bot):
    await bot.add_cog(ChatRoleEvent(bot))
    logging.info('[Extension] ChatRoleEvent module loaded')


async def teardown(bot):
    await bot.remove_cog('ChatRoleEvent')
    logging.info('[Extension] ChatRoleEvent module unloaded')
