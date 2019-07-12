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

        self.punDM = 'You have received a moderation action on the /r/NintendoSwitch Discord server.\n' \
            'Action: **{}**\n' \
            'Reason:\n```{}```\n' \
            'Responsible moderator: {} ({})\n' \
            'If you have questions concerning this matter, please feel free to contact the respective moderator that took this action or another member of the moderation team.\n\n' \
            'Please do not respond to this message, I cannot reply.'

    @commands.command(name='ban', aliases=['banid', 'forceban'])
    @commands.has_any_role(config.moderator, config.eh)
    async def _banning(self, ctx, user: typing.Union[discord.Member, int], *, reason='-No reason specified-'):
        userid = user if (type(user) is int) else user.id
        await utils.issue_pun(userid, ctx.author.id, 'ban', reason=reason)

        username = userid if (type(user) is int) else f'{str(user)}'
        user = discord.Object(id=userid) if (type(user) is int) else user # If not a user, manually contruct a user object

        embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Ban | {username}')
        embed.add_field(name='User', value=f'<@{userid}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        try:
            await user.send(self.punDM.format('Ban', reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        await ctx.guild.ban(user, reason=f'Ban action by {str(user)}')
        await self.modLogs.send(embed=embed)
        return await ctx.send(f'{config.greenTick} {username} has been successfully banned')

    @commands.command(name='unban')
    @commands.has_any_role(config.moderator, config.eh)
    async def _unbanning(self, ctx, user: int, *, reason='-No reason specified-'):
        db = mclient.bowser.puns
        userObj = discord.Object(id=user)
        try:
            await ctx.guild.fetch_ban(userObj)

        except discord.NotFound:
            return await ctx.send(f'{config.redTick} {user} is not currently banned')

        await ctx.guild.unban(userObj)
        db.find_one_and_update({'user': user, 'type': 'ban', 'active': True}, {'$set':{
            'active': False
        }})
        await utils.issue_pun(user,ctx.author.id, 'unban', reason, active=False)
        return await ctx.send(f'{config.greenTick} {user} has been unbanned')

    @commands.command(name='mute')
    @commands.has_any_role(config.moderator, config.eh)
    async def _muting(self, ctx, member: discord.Member, duration, *, reason='-No reason specified-'):
        db = mclient.bowser.puns
        if db.find_one({'user': member.id, 'type': 'mute', 'active': True}):
            return await ctx.send(f'{config.redTick} {str(member)} ({member.id}) is already muted')

        muteRole = ctx.guild.get_role(config.mute)
        try:
            _duration = await utils.resolve_duration(duration)

        except KeyError:
            return await ctx.send(f'{config.redTick} Invalid duration passed')

        await utils.issue_pun(member.id, ctx.author.id, 'mute', reason, int(_duration.timestamp()))
        await member.add_roles(muteRole)
        try:
            await member.send(self.punDM.format(f'Mute ({duration})', reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass
        return await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully muted')

    @commands.command(name='unmute')
    @commands.has_any_role(config.moderator, config.eh)
    async def _unmuting(self, ctx, member: discord.Member, *, reason='-No reason specified-'): # TODO: Allow IDs to be unmuted (in the case of not being in the guild)
        db = mclient.bowser.puns
        muteRole = ctx.guild.get_role(config.mute)
        action = db.find_one_and_update({'user': member.id, 'type': 'mute', 'active': True}, {'$set':{
            'active': False
        }})
        if not action:
            return await ctx.send(f'{config.redTick} Cannot unmute {str(member)} ({member.id}), they are not currently muted')

        await utils.issue_pun(member.id, ctx.author.id, 'unmute', reason, active=False)
        await member.remove_roles(muteRole)
        try:
            await member.send(self.punDM.format(f'Unmute', reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass
        return await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully unmuted')

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
        try:
            await member.send(self.punDM.format(warnText[warnLevel], reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except discord.Forbidden: # User has DMs off
            pass

        await self.modLogs.send(embed=embed)
        return await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully warned; they are now tier {warnLevel + 1}')

    @_warning.command(name='clear')
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning_clear(self, ctx, member: discord.Member, *, reason):
        db = mclient.bowser.puns
        tierLevel = {
            1: ctx.guild.get_role(config.warnTier1),
            2: ctx.guild.get_role(config.warnTier2),
            3: ctx.guild.get_role(config.warnTier3)
        }
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
            tierInt = int(x['type'][-1:])
            await member.remove_roles(tierLevel[tierInt])

        embed = discord.Embed(color=discord.Color(0x18EE1C), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Warnings cleared | {str(member)}')
        embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        await utils.issue_pun(member.id, ctx.author.id, 'clear', reason, active=False)
        await self.modLogs.send(embed=embed)
        try:
            await member.send(self.punDM.format('Warning level has been reset', reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except discord.Forbidden: # User has DMs off
            pass
        return await ctx.send(f'{config.greenTick} Warnings have been marked as inactive for {str(member)} ({member.id})')

    @_warning.command(name='level')
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning_setlevel(self, ctx, member: discord.Member, tier: int, *, reason):
        if tier not in [1, 2, 3]:
            return await ctx.send(f'{config.redTick} Invalid tier number provided')
    
        db = mclient.bowser.puns
        tierLevel = {
            1: ctx.guild.get_role(config.warnTier1),
            2: ctx.guild.get_role(config.warnTier2),
            3: ctx.guild.get_role(config.warnTier3)
        }
        embedColor = {
            1: discord.Color(0xFFFA1C),
            2: discord.Color(0xFF9000),
            3: discord.Color(0xD0021B)
        }
        warnText = {
            1: 'First warning',
            2: 'Second warning',
            3: 'Third warning'
        }

        puns = db.find({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )
        if puns:
            for x in puns:
                db.update_one({'_id': x['_id']}, {'$set': {
                    'active': False
                }})
                tierInt = int(x['type'][-1:])
                await member.remove_roles(tierLevel[tierInt])

        embed = discord.Embed(color=embedColor[tier], timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{warnText[tier]} | {str(member)}')
        embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason)

        await member.add_roles(tierLevel[tier])
        await utils.issue_pun(member.id, ctx.author.id, f'tier{tier}', reason, context='level_set')
        await self.modLogs.send(embed=embed)
        try:
            await member.send(self.punDM.format(warnText[tier], reason, str(ctx.author), f'<@{ctx.author.id}>'))
        except discord.Forbidden: # User has DMs off
            pass
        return await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully warned; they are now tier {tier}')

    @_warning.error
    @_warning_clear.error
    @_warning_setlevel.error
    async def mod_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing argument')

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(f'{config.redTick} Invalid arguments')

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured. This has been logged.')
            raise error

def setup(bot):
    bot.add_cog(Moderation(bot))
    logging.info('[Extension] Moderation module loaded')

def teardown(bot):
    bot.remove_cog('Moderation')
    logging.info('[Extension] Moderation module unloaded')
