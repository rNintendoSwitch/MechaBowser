import asyncio
import logging
import time
import typing
from datetime import date, datetime, timedelta, timezone

import config
import discord
import pymongo
import pytz
from discord import app_commands
from discord.ext import commands

import tools

mclient = pymongo.MongoClient(config.mongoURI)


class StatCommands(commands.Cog, name='Statistic Commands'):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class StatsCommand(app_commands.Group):
        pass

    stats_group = StatsCommand(name='stats', description='View various statistics about the server and it\'s members')

    async def _stats_server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> typing.List[app_commands.Choice[str]]:
        if not current:
            # Blank, suggest today's date
            isodate = date.today().isoformat()
            return [app_commands.Choice(name=isodate, value=isodate)]

        else:
            return []

    @stats_group.command(name='server')
    @app_commands.autocomplete(start=_stats_server_autocomplete)
    @app_commands.autocomplete(end=_stats_server_autocomplete)
    @app_commands.describe(
        start='The start date to search, in YYYY-MM-DD format', end='The end date to search, in YYYY-MM-DD format'
    )
    async def _stats_server(
        self, interaction: discord.Interaction, start: typing.Optional[str] = None, end: typing.Optional[str] = None
    ):
        '''Returns server activity statistics'''
        await interaction.response.send_message('One moment, crunching message and channel data...')

        try:
            searchDate = (
                datetime.now(tz=timezone.utc)
                if not start
                else datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            )
            searchDate = searchDate.replace(hour=0, minute=0, second=0)
            endDate = (
                searchDate + timedelta(days=30)
                if not end
                else datetime.strptime(end, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            )
            endDate = endDate.replace(hour=23, minute=59, second=59)

        except ValueError:
            return await interaction.edit_original_response(
                content=f'{config.redTick} Invalid date provided. Please make sure it is in the format of `yyyy-mm-dd`'
            )

        if not start:
            query = {'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))}}
            messages = mclient.bowser.messages.find(query)

        else:
            if endDate <= searchDate:
                return await interaction.edit_original_response(
                    content=f'{config.redTick} Invalid dates provided. The end date cannot be before the starting date. `/stats server [starting date] [ending date]`'
                )

            query = {'timestamp': {'$gte': searchDate.timestamp(), '$lte': endDate.timestamp()}}
            messages = mclient.bowser.messages.find(query)

        msgCount = mclient.bowser.messages.count_documents(query)
        channelCounts = {}
        userCounts = {}
        for message in messages:
            if message['channel'] not in channelCounts.keys():
                channelCounts[message['channel']] = 1

            else:
                channelCounts[message['channel']] += 1

            if message['author'] not in userCounts.keys():
                userCounts[message['author']] = 1

            else:
                userCounts[message['author']] += 1

        if not start:
            puns = mclient.bowser.puns.count_documents(
                {
                    'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))},
                    'type': {'$nin': ['unmute', 'unblacklist', 'note']},
                }
            )

        else:
            puns = mclient.bowser.puns.count_documents(
                {
                    'timestamp': {'$gte': searchDate.timestamp(), '$lte': endDate.timestamp()},
                    'type': {'$nin': ['unmute', 'unblacklist', 'note']},
                }
            )

        topChannels = sorted(channelCounts.items(), key=lambda x: x[1], reverse=True)[
            0:5
        ]  # Get a list of tuple sorting by most active channel to least, and only include top 5
        topChannelsList = []
        for x in topChannels:
            channelObj = self.bot.get_channel(x[0])
            if channelObj:
                topChannelsList.append(f'{self.bot.get_channel(x[0]).mention} ({x[1]})')

            else:
                topChannelsList.append(f'*Deleted channel* ({x[1]})')

        await interaction.edit_original_response(content='One moment, crunching member data...')
        netJoins = 0
        netLeaves = 0
        for member in mclient.bowser.users.find({'joins': {'$ne': []}}):
            for join in member['joins']:
                if not start and (searchDate.timestamp() - (60 * 60 * 24 * 30)) <= join <= endDate.timestamp():
                    netJoins += 1

                elif start and searchDate.timestamp() <= join <= endDate.timestamp():
                    netJoins += 1

            for leave in member['leaves']:
                if not start and (searchDate.timestamp() - (60 * 60 * 24 * 30)) <= leave <= endDate.timestamp():
                    netLeaves += 1

                elif start and searchDate.timestamp() <= leave <= endDate.timestamp():
                    netLeaves += 1

        activeChannels = ', '.join(topChannelsList)
        premiumTier = 'No tier' if interaction.guild.premium_tier == 0 else f'Tier {interaction.guild.premium_tier}'

        dayStr = (
            'In the last 30 days'
            if not start and not end
            else 'Between ' + searchDate.strftime('%Y-%m-%d') + ' and ' + endDate.strftime('%Y-%m-%d')
        )
        netMembers = netJoins - netLeaves
        netMemberStr = (
            f':chart_with_upwards_trend: **+{netMembers}** net members joined\n'
            if netMembers >= 0
            else f':chart_with_downwards_trend: **{netMembers}** net members left\n'
        )

        embed = discord.Embed(
            title=f'{interaction.guild.name} Statistics',
            description=f'Current member count is **{interaction.guild.member_count}**\n*__{dayStr}...__*\n\n'
            f':incoming_envelope: **{msgCount}** messages have been sent\n:information_desk_person: **{len(userCounts)}** members were active\n'
            f'{netMemberStr}:hammer: **{puns}** punishment actions were handed down\n\n:bar_chart: The most active channels by message count were {activeChannels}',
            color=0xD267BA,
        )
        embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.add_field(
            name='Guild information',
            value=f'**Boost level:** {premiumTier}\n**Number of boosters:** {interaction.guild.premium_subscription_count}',
        )

        return await interaction.edit_original_response(content=None, embed=embed)

    @stats_group.command(name='users')
    async def _stats_users(self, interaction: discord.Interaction):
        '''Returns most active users'''
        await interaction.response.send_message('One moment, crunching the numbers...')
        messages = mclient.bowser.messages.find({'timestamp': {'$gt': (int(time.time()) - (60 * 60 * 24 * 30))}})
        msgCounts = {}
        for message in messages:
            if message['author'] not in msgCounts.keys():
                msgCounts[message['author']] = 1

            else:
                msgCounts[message['author']] += 1

        topSenders = sorted(msgCounts.items(), key=lambda x: x[1], reverse=True)[
            0:25
        ]  # Get a list of tuple sorting by most message to least, and only include top 25
        embed = discord.Embed(
            title='Top User Statistics',
            description='List of the 25 highest message senders and their count during the last 30 days\n',
            color=0xD267BA,
        )
        for x in topSenders:
            msgUser = interaction.guild.get_member(x[0])
            if not msgUser:
                msgUser = await self.bot.fetch_user(x[0])

            embed.add_field(name=str(msgUser), value=str(x[1]))

        return await interaction.edit_original_response(content=None, embed=embed)

    @stats_group.command(name='roles')
    @app_commands.describe(role='A specific role to provide detailed statistics on')
    async def _stats_roles(
        self, interaction: discord.Interaction, role: typing.Optional[discord.Role]
    ):  # TODO: create and pull role add/remove data from events
        '''Returns statistics on the ownership of roles'''
        await interaction.response.send_message('One moment, crunching the numbers...')
        if role:
            lines = []
            desc = f'There are currently **{len(role.members)}** members with the **{role.name}** role:\n\n'
            for member in role.members:
                lines.append(f'* {member} ({member.id})')

            title = f'{interaction.guild.name} Role Statistics'
            fields = tools.convert_list_to_fields(lines)
            view = tools.PaginatedEmbed(
                interaction=interaction, fields=fields, title=title, description=desc, color=0xD267BA
            )
            return await interaction.edit_original_response(
                content='Here is the requested list of members with that role:', view=view
            )

        else:
            roleCounts = []
            for role in reversed(interaction.guild.roles):
                roleCounts.append(f'**{role.name}:** {len(role.members)}')

            roleList = '\n'.join(roleCounts)
            embed = discord.Embed(
                title=f'{interaction.guild.name} Role Statistics',
                description=f'Server role list and respective member count\n\n{roleList}',
                color=0xD267BA,
            )

            return await interaction.edit_original_response(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(StatCommands(bot))
    logging.info('[Extension] Statistics module loaded')


async def teardown(bot):
    await bot.remove_cog('StatCommands')
    logging.info('[Extension] Statistics module unloaded')
