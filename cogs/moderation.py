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

    @commands.group(name='warn', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning(self, ctx, member: discord.Member, *, reason):
        db = mclient.bowser.puns
        warnLevel = 0
        tierLevel = {
            0: ctx.guild.get_role(config.warnTier1),
            1: ctx.guild.get_role(config.warnTier2),
            2: ctx.guild.get_role(config.warnTier3)
        }
        embedColor = {
            0: discord.Color(0xFFFA1C),
            1: discord.Color(0xFF9000),
            2: discord.Color(0xD0021B)
        }
        warnText = {
            0: 'First warning',
            1: 'Second warning',
            2: 'Third warning'
        }

        puns = db.find_one({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )
        if puns: # Active punishments, give tier 2/3
            if puns['type'] == 'tier3':
                return await ctx.send(f'{config.redTick} That user is already warn tier 3')

            db.update_one({'_id': puns['_id']}, {'$set': {
                'active': False
            }})
            warnLevel = 2 if puns['type'] == 'tier2' else 1

        embed = discord.Embed(color=embedColor[warnLevel], timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{warnText[warnLevel]} | {str(member)}')
        embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        for role in member.roles:
            if role in [tierLevel[0], tierLevel[1], tierLevel[2]]:
                await member.remove_roles(role, reason='Warn action performed by moderator')

        await member.add_roles(tierLevel[warnLevel], reason='Warn action performed by moderator')
        await utils.issue_pun(member.id, ctx.author.id, f'tier{warnLevel + 1}', reason)

        await self.modLogs.send(embed=embed)
        return await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully warned; they are now tier {warnLevel + 1}')

    @_warning.command(name='clear')
    async def _warning_clear(self, ctx, member: discord.Member, *, reason):
        db = mclient.bowser.puns
        puns = db.find({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )

        if not puns.count():
            return await ctx.send(f'{config.redTick} That user has no active warnings')

        for x in puns:
            db.update_one({'_id': x['_id']}, {'$set': {
                'active': False
            }})

        embed = discord.Embed(color=discord.Color(0x18EE1C), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Warnings cleared | {str(member)}')
        embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        await utils.issue_pun(member.id, ctx.author.id, 'clear', reason, active=False)
        await self.modLogs.send(embed=embed)
        return await ctx.send(f'{config.greenTick} Warnings have been marked as inactive for {str(member)} ({member.id})')

    @_warning.error
    @_warning_clear.error
    async def mod_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing argument')

        #elif isinstance(error, commands.BadArgument):

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured. This has been logged.')
            raise error

def setup(bot):
    bot.add_cog(Moderation(bot))
    logging.info('[Extension] Moderation module loaded')

def teardown(bot):
    bot.remove_cog('Moderation')
    logging.info('[Extension] Moderation module unloaded')
