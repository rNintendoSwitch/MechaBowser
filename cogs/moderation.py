import asyncio
import logging
import datetime
import time
import typing

import pymongo
import discord
from discord.ext import commands

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

serverLogs = None
modLogs = None
Client = None

@commands.command(name='ban', aliases=['banid', 'forceban'])
@commands.has_any_role(config.moderator, config.eh)
async def _banning(ctx, user: typing.Union[discord.User, int], *args):
    if len(args) < 1:
        return await ctx.send(':warning: A reason is required')

    userid = user if (type(user) is int) else user.id
    db = mclient.fil.users
    doc = db.find_one({'_id': userid})

    reason = ''
    for x in args:
        reason += f'{x} '


    if not doc and user:
        # Serious issue, all users should be in the database. Abort
        logging.critical(f'Member {userid} does not exist in the database!')
        return await ctx.send(':warning: Unable to find user. This has been logged.')
    
    elif not doc:
        # Lets make a new doc for this user
        db.insert_one({
            '_id': userid,
            'messages': 0,
            'last_message': None,
            'roles': [],
            'punishments': [{
                'moderator': ctx.author.id,
                'type': 'ban',
                'timestamp': int(time.time()),
                'expiry': None,
                'reason': reason[:-1],
                'active': True
            }]
        })

    else:
        db.update_one({'_id': userid}, {'$push': {
            'punishments': {
                'moderator': ctx.author.id,
                'type': 'ban',
                'timestamp': int(time.time()),
                'expiry': None,
                'reason': reason[:-1],
                'active': True
            }
        }})

    user = discord.Object(id=userid) if (type(user) is int) else user # If not a user, manually contruct a user object
    username = userid if (type(user) is int) else f'{user.name}#{user.discriminator}'

    embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'Ban | {username}')
    embed.add_field(name='User', value=f'<@{userid}>', inline=True)
    embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
    embed.add_field(name='Reason', value=reason)

    await ctx.guild.ban(user, reason=f'Ban action by {ctx.author.id}#{ctx.author.discriminator}')
    await modLogs.send(embed=embed)
    return await ctx.send(':heavy_check_mark: User banned')

@commands.command()
@commands.has_any_role(config.moderator, config.eh)
async def warn(ctx, member: discord.Member, *args):
    if len(args) < 1:
        return await ctx.send(':warning: A reason is required')

    reason = ''
    for x in args:
        reason += f'{x} '
    db = mclient.fil.users
    user = db.find_one({'_id': member.id}) # TODO: Mark tier unactive after escalating

    if not user:
        # Serious issue, all users should be in the database. Abort
        logging.critical(f'Member {member.id} does not exist in the database!')
        return await ctx.send(':warning: Unable to find user. This has been logged.')

    tier1 = ctx.guild.get_role(config.warnTier1)
    tier2 = ctx.guild.get_role(config.warnTier2)
    tier3 = ctx.guild.get_role(config.warnTier3)

    tierStr = {
        0: 'tier1',
        1: 'tier2',
        2: 'tier3'
    }

    Roles = member.roles
    warnLevel = 0
    for role in Roles:
        if role == tier3:
            return await ctx.send(f':warning: {member.name}#{member.discriminator} is already at the highest warn tier!')
        
        elif role == tier2:
            warnLevel = 2
            Roles.remove(role)
            Roles.append(tier3)

            embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'Third Warning | {member.name}#{member.discriminator}')
            embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
            embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
            embed.add_field(name='Reason', value=reason[:-1])

            puns = user['punishments']
            updatedPuns = []
            for obj in puns:
                if not obj['active']:
                    updatedPuns.append(obj)
                    continue
                
                if obj['type'] != 'tier2':
                    continue

                obj['active'] = False
                updatedPuns.append(obj)
        
        elif role == tier1:
            warnLevel = 1
            Roles.remove(role)
            Roles.append(tier2)
            
            embed = discord.Embed(color=discord.Color(0xFF9000), timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'Second Warning | {member.name}#{member.discriminator}')
            embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
            embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
            embed.add_field(name='Reason', value=reason[:-1])

            puns = user['punishments']
            updatedPuns = []
            for obj in puns:
                if not obj['active']:
                    updatedPuns.append(obj)
                    continue
                
                if obj['type'] != 'tier1':
                    continue

                obj['active'] = False
                updatedPuns.append(obj)
    
    if warnLevel == 0:
        Roles.append(tier1)

        embed = discord.Embed(color=discord.Color(0xFFFA1C), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'First Warning | {member.name}#{member.discriminator}')
        embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
        embed.add_field(name='Moderator', value=f'<@{ctx.author.id}>', inline=True)
        embed.add_field(name='Reason', value=reason[:-1])

        db.update_one({'_id': member.id}, {'$push': {
            'punishments': {
                'moderator': ctx.author.id,
                'type': 'tier1',
                'timestamp': int(time.time()),
                'expiry': None,
                'reason': reason[:-1],
                'active': True
            }
        }})

    else:
        db.update_one({'_id': member.id}, {'$push': {
            'punishments': {
                'moderator': ctx.author.id,
                'type': tierStr[warnLevel],
                'timestamp': int(time.time()),
                'expiry': None,
                'reason': reason[:-1],
                'active': True
            }
        }})
    
    await member.edit(roles=Roles, reason=f'Warning action by {ctx.author.name}#{ctx.author.discriminator}')
    await modLogs.send(embed=embed)

    return await ctx.send(f':heavy_check_mark: Issued a Tier {warnLevel + 1} warning to {member.name}#{member.discriminator}')

@warn.error
async def warn_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(':warning: Missing argument')

    else:
        await ctx.send(':warning: An unknown exception has occured. This has been logged.')
        raise error

def setup(bot):
    global serverLogs
    global modLogs
    global Client

    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)
    Client = bot

    bot.add_command(warn)
    bot.add_command(_banning)
    logging.info('Moderation module loaded')
