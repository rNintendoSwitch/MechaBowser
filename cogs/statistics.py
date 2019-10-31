import asyncio
import logging
import time
import typing

import pymongo
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
        "stats users\n    -Returns most active users in the last 30 days\n\n" \
        "stats roles\n    -Returns statistics on the ownership of roles\n\n" \
        "stats emoji\n    -Returns stats on emoji usage\n```")

    @_stats.command(name='server')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_server(self, ctx):
        messages = mclient.bowser.messages.find({
                    'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))}
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

        puns = mclient.bowser.puns.find({
                    'timestamp': {'$gte': (int(time.time()) - (60 * 60 * 24 * 30))}
            }).count()
        topChannels = sorted(channelCounts.items(), key=lambda x: x[1], reverse=True)[0:5] # Get a list of tuple sorting by most active channel to least, and only include top 5
        topChannelsList = []
        for x in topChannels:
            topChannelsList.append(f'{self.bot.get_channel(x[0]).mention} ({x[1]})')

        activeChannels = ', '.join(topChannelsList)
        premiumTier = 'No tier' if ctx.guild.premium_tier == 0 else f'Tier {ctx.guild.premium_tier}'

        embed = discord.Embed(title=f'{ctx.guild.name} Statistics', description=f'Current member count is **{ctx.guild.member_count}**\n*__In the last 30 days...__*\n\n' \
            f':incoming_envelope:**{msgCount}** messages have been sent\n:information_desk_person:**{len(userCounts)}** members were active\n' \
            f':hammer:**{puns}** punishment actions were handed down\n:bar_chart: The most active channels by message count were {activeChannels}', color=0xD267BA)
        embed.set_thumbnail(url=ctx.guild.icon_url)
        embed.add_field(name='Guild features', value=f'**Guild flags:** {", ".join(ctx.guild.features)}\n' \
            f'**Boost level:** {premiumTier}\n**Number of boosters:** {ctx.guild.premium_subscription_count}')

        return await ctx.send(embed=embed)

    @_stats.command(name='users')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_users(self, ctx):
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

        return await ctx.send(embed=embed)

    @_stats.command(name='roles', aliases=['role'])
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_roles(self, ctx, *, role: typing.Optional[typing.Union[discord.Role, int, str]]): # TODO: create and pull role add/remove data from events
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
            message = await ctx.send(embed=embed)
            page = 1 # pylint: disable=unused-variable
            stop = time.time() + 1800

            await message.add_reaction('⬅')
            await message.add_reaction('➡')
            await message.add_reaction('⏹')

            def check(reaction, user):
                if user.id != ctx.author.id or reaction.message.id != message.id:
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
                    await message.edit(embed=embed)

                except asyncio.TimeoutError:
                    pass

            await message.clear_reactions()

        else:
            roleCounts = []
            for role in reversed(ctx.guild.roles):
                roleCounts.append(f'**{role.name}:** {len(role.members)}')

            roleList = '\n'.join(roleCounts)
            embed = discord.Embed(title=f'{ctx.guild.name} Role Statistics', description=f'Server role list and respective member count\n\n{roleList}', color=0xD267BA)

            return await ctx.send(embed=embed)

    @_stats.command(name='channels')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_channels(self, ctx):
        pass

    @_stats.command(name='emoji')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_emoji(self, ctx):
        pass

    @_stats.command(name='statuses')
    @commands.has_any_role(config.moderator, config.eh)
    async def _stats_statuses(self, ctx):

def setup(bot):
    bot.add_cog(StatCommands(bot))
    logging.info('[Extension] Statistics module loaded')

def teardown(bot):
    bot.remove_cog('ChatControl')
    logging.info('[Extension] Statistics module unloaded')