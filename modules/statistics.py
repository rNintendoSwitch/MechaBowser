import asyncio
import logging
import time
import typing
import datetime

import pymongo
import pytz
import discord
from discord.ext import commands, tasks

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class StatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name='stats', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats(self, ctx):
        return await ctx.send("Valid subcommands:```\n" \
        "stats server\n    -Returns server activity statistics\n\n" \
        "stats users\n    -Returns most active users\n\n" \
        "stats roles\n    -Returns statistics on the ownership of roles\n\n" \
        "stats emoji\n    -Returns stats on emoji usage\n\n" \
        "stats channels\n    -Returns most active channels" \
        "stats statuses\n    -Returns user statuses over the last 24 hours```")

    @_stats.command(name='server')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_server(self, ctx, start_date=None, end_date=None):
        msg = await ctx.send('One moment, crunching message and channel data...')

        try:
            searchDate = datetime.datetime.utcnow() if not start_date else datetime.datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            searchDate = searchDate.replace(hour=0, minute=0, second=0)
            endDate = searchDate + datetime.timedelta(days=30) if not end_date else datetime.datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
            endDate = endDate.replace(hour=23, minute=59, second=59)

        except ValueError:
            return await msg.edit(content=f'{config.redTick} Invalid date provided. Please make sure it is in the format of `yyyy-mm-dd`')

        if not start_date:
            messages = mclient.bowser.messages.find({
                        'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))}
                })

        else:
            if endDate <= searchDate:
                return await msg.edit(content=f'{config.redTick} Invalid dates provided. The end date is before the starting date. `{ctx.prefix}stats server [starting date] [ending date]`')

            messages = mclient.bowser.messages.find({
                        'timestamp': {'$gte': searchDate.timestamp(), '$lte': endDate.timestamp()}
                })

        msgCount = messages.count()
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



        if not start_date:
            puns = mclient.bowser.puns.find({
                        'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))},
                        'type': {'$nin': ['unmute', 'unblacklist', 'note']}
                }).count()

        else:
            puns = mclient.bowser.puns.find({
                        'timestamp': {'$gte': searchDate.timestamp(), '$lte': endDate.timestamp()},
                        'type': {'$nin': ['unmute', 'unblacklist', 'note']}
                }).count()

        topChannels = sorted(channelCounts.items(), key=lambda x: x[1], reverse=True)[0:5] # Get a list of tuple sorting by most active channel to least, and only include top 5
        topChannelsList = []
        for x in topChannels:
            channelObj = self.bot.get_channel(x[0])
            if channelObj:
                topChannelsList.append(f'{self.bot.get_channel(x[0]).mention} ({x[1]})')

            else:
                topChannelsList.append(f'*Deleted channel* ({x[1]})')

        await msg.edit(content='One moment, crunching member data...')
        netJoins = 0
        netLeaves = 0
        for member in mclient.bowser.users.find({'joins': {'$ne': []}}):
            for join in member['joins']:
                if not start_date and (searchDate.timestamp() - (60 * 60 * 24 * 30)) <= join <= endDate.timestamp():
                    netJoins += 1

                elif start_date and searchDate.timestamp() <= join <= endDate.timestamp():
                    netJoins += 1

            for leave in member['leaves']:
                if not start_date and (searchDate.timestamp() - (60 * 60 * 24 * 30)) <= leave <= endDate.timestamp():
                    netLeaves += 1

                elif start_date and searchDate.timestamp() <= leave <= endDate.timestamp():
                    netLeaves += 1

        activeChannels = ', '.join(topChannelsList)
        premiumTier = 'No tier' if ctx.guild.premium_tier == 0 else f'Tier {ctx.guild.premium_tier}'

        dayStr = 'In the last 30 days' if not start_date else 'Between ' + searchDate.strftime('%Y-%m-%d') + ' and ' + endDate.strftime('%Y-%m-%d')
        netMembers = netJoins - netLeaves
        netMemberStr = f':chart_with_upwards_trend: **+{netMembers}** net members joined\n' if netMembers >= 0 else f':chart_with_downwards_trend: **{netMembers}** net members left\n'

        embed = discord.Embed(title=f'{ctx.guild.name} Statistics', description=f'Current member count is **{ctx.guild.member_count}**\n*__{dayStr}...__*\n\n' \
            f':incoming_envelope: **{msgCount}** messages have been sent\n:information_desk_person: **{len(userCounts)}** members were active\n' \
            f'{netMemberStr}:hammer: **{puns}** punishment actions were handed down\n\n:bar_chart: The most active channels by message count were {activeChannels}', color=0xD267BA)
        embed.set_thumbnail(url=ctx.guild.icon_url)
        embed.add_field(name='Guild features', value=f'**Guild flags:** {", ".join(ctx.guild.features)}\n' \
            f'**Boost level:** {premiumTier}\n**Number of boosters:** {ctx.guild.premium_subscription_count}')

        return await msg.edit(content=None, embed=embed)

    @_stats.command(name='users')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_users(self, ctx):
        msg = await ctx.send('One moment, crunching the numbers...')
        messages = mclient.bowser.messages.find({
                    'timestamp': {'$gt': (int(time.time()) - (60 * 60 * 24 * 30))}
            })
        msgCounts = {}
        for message in messages:
            if message['author'] not in msgCounts.keys():
                msgCounts[message['author']] = 1

            else:
                msgCounts[message['author']] += 1

        topSenders = sorted(msgCounts.items(), key=lambda x: x[1], reverse=True)[0:25] # Get a list of tuple sorting by most message to least, and only include top 25
        embed = discord.Embed(title='Top User Statistics', description='List of the 25 highest message senders and their count during the last 30 days\n', color=0xD267BA)
        for x in topSenders:
            msgUser = ctx.guild.get_member(x[0])
            if not msgUser:
                msgUser = await self.bot.fetch_user(x[0])

            embed.add_field(name=str(msgUser), value=str(x[1]))

        return await msg.edit(content=None, embed=embed)

    @_stats.command(name='roles', aliases=['role'])
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_roles(self, ctx, *, role: typing.Optional[typing.Union[discord.Role, int, str]]): # TODO: create and pull role add/remove data from events
        msg = await ctx.send('One moment, crunching the numbers...')
        if role:
            if type(role) is int:
                role = ctx.guild.get_role(role)
                if not role:
                    return await ctx.send(f'{config.redTick} There is no role by that ID')

            elif type(role) is str:
                role = discord.utils.get(ctx.guild.roles, name=role)
                if not role:
                    return await ctx.send(f'{config.redTick} There is no role by that name')

            chunks = []
            header = f'There are currently **{len(role.members)}** users with the **{role.name}** role:\n\n'
            for member in role.members:
                chunks.append(f'* {member} ({member.id})\n')

            embed = discord.Embed(title=f'{ctx.guild.name} Role Statistics', color=0xD267BA)
            embed.add_field(name='Instructions', value='Use :arrow_right: and :arrow_left: to scroll between pages. :stop_button: To end')
            newPage, pages = await utils.embed_paginate(chunks, header=header)
            embed.description = newPage
            await msg.edit(content=None, embed=embed)
            page = 1 # pylint: disable=unused-variable
            stop = time.time() + 1800

            await msg.add_reaction('⬅')
            await msg.add_reaction('➡')
            await msg.add_reaction('⏹')

            def check(reaction, user):
                if user.id != ctx.author.id or reaction.message.id != msg.id:
                    return False

                return True

            while time.time() <= stop:
                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=30, check=check)
                    if reaction.emoji == '⬅':
                        if page == 1: # Don't switch down past page 1
                            await reaction.remove(user)
                            continue

                        else:
                            await reaction.remove(user)
                            page -= 1

                    elif reaction.emoji == '➡':
                        if page == pages: # Don't exceed last page
                            await reaction.remove(user)
                            continue

                        else:
                            await reaction.remove(user)
                            page += 1

                    elif reaction.emoji == '⏹':
                        break

                    else:
                        continue

                    newPage, pages = await utils.embed_paginate(chunks, header=header, page=page)
                    embed.description = newPage
                    await msg.edit(embed=embed)

                except asyncio.TimeoutError:
                    pass

            await msg.clear_reactions()

        else:
            roleCounts = []
            for role in reversed(ctx.guild.roles):
                roleCounts.append(f'**{role.name}:** {len(role.members)}')

            roleList = '\n'.join(roleCounts)
            embed = discord.Embed(title=f'{ctx.guild.name} Role Statistics', description=f'Server role list and respective member count\n\n{roleList}', color=0xD267BA)

        return await msg.edit(content=None, embed=embed)

    @_stats.command(name='channels')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_channels(self, ctx):
        return await ctx.send(f'{config.redTick} Channel statistics are not ready for use')

    @_stats.command(name='emoji')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_emoji(self, ctx):
        return await ctx.send(f'{config.redTick} Emoji statistics are not ready for use')

    @_stats.command(name='statuses')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_statuses(self, ctx):
        return await ctx.send(f'{config.redTick} Status statistics are not ready for use')

    @_stats.error
    @_stats_channels.error
    @_stats_emoji.error
    @_stats_roles.error
    @_stats_server.error
    @_stats_statuses.error
    @_stats_users.error
    async def stat_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.', delete_after=15)
            raise error

def setup(bot):
    bot.add_cog(StatCommands(bot))
    logging.info('[Extension] Statistics module loaded')

def teardown(bot):
    bot.remove_cog('ChatControl')
    logging.info('[Extension] Statistics module unloaded')