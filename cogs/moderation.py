import asyncio
import logging
import datetime
import time
import typing

import pymongo
import discord
from discord.ext import commands

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)

    @commands.command(name='ban', aliases=['banid', 'forceban'])
    @commands.has_any_role(config.moderator, config.eh)
    async def _banning(self, ctx, user: typing.Union[discord.User, int], *, reason='-No reason specified-'):
        userid = user if (type(user) is int) else user.id
        await utils.issue_pun(userid, ctx.author.id, 'ban', reason=reason)

        user = discord.Object(id=userid) if (type(user) is int) else user # If not a user, manually contruct a user object
        username = userid if (type(user) is int) else f'{str(user)}'

        embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Ban | {username}')
        embed.add_field(name='User', value=f'<@{userid}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        await ctx.guild.ban(user, reason=f'Ban action by {str(user)}')
        await self.modLogs.send(embed=embed)
        return await ctx.send(':heavy_check_mark: User banned')

    @commands.command(name='warn')
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning(self, ctx, member: discord.Member, *, reason):
        tier1 = ctx.guild.get_role(config.warnTier1)
        tier2 = ctx.guild.get_role(config.warnTier2)
        tier3 = ctx.guild.get_role(config.warnTier3)

        warnLevel = 0
        Roles = member.roles
        for role in Roles:
            if role == tier3:
                return await ctx.send(f':warning: {member.name}#{member.discriminator} is already at the highest warn tier!')
        
            elif role == tier2:
                warnLevel = 2

                embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Third Warning | {str(member)}')
                embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
                embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
                embed.add_field(name='Reason', value=reason)

                await utils.issue_pun(member.id, ctx.author.id, 'tier3', reason=reason)
                await member.remove_roles(tier2, reason='Warn action performed by moderator')
                await member.add_roles(tier3, reason='Warn action performed by moderator')
        
            elif role == tier1:
                warnLevel = 1
            
                embed = discord.Embed(color=discord.Color(0xFF9000), timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Second Warning | {str(member)}')
                embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
                embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
                embed.add_field(name='Reason', value=reason)
                
                await utils.issue_pun(member.id, ctx.author.id, 'tier2', reason=reason)
                await member.remove_roles(tier1, reason='Warn action performed by moderator')
                await member.add_roles(tier2, reason='Warn action performed by moderator')
    
        if warnLevel == 0:

            embed = discord.Embed(color=discord.Color(0xFFFA1C), timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'First Warning | {str(member)}')
            embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
            embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
            embed.add_field(name='Reason', value=reason)

            await utils.issue_pun(member.id, ctx.author.id, 'tier1', reason=reason)
            await member.add_roles(tier1, reason='Warn action performed by moderator')
    
        await self.modLogs.send(embed=embed)

        return await ctx.send(f':heavy_check_mark: Issued a Tier {warnLevel + 1} warning to {str(member)}')

    @_warning.error
    async def warn_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(':warning: Missing argument')

        else:
            await ctx.send(':warning: An unknown exception has occured. This has been logged.')
            raise error

def setup(bot):
    bot.add_cog(Moderation(bot))
    logging.info('[Extension] Moderation module loaded')

def teardown(bot):
    bot.remove_cog('Moderation')
    logging.info('[Extension] Utility module unloaded')
