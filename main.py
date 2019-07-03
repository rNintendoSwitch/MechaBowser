import asyncio
import logging
import datetime
import time
import copy
from collections import Counter

import pymongo
import discord
from discord.ext import commands, tasks

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)
activityStatus = discord.Activity(type=discord.ActivityType.playing, name='bot dev with MattBSG')
bot = commands.Bot('()', max_messages=30000, fetch_offline_members=True, activity=activityStatus)

LOG_FORMAT = '%(levelname)s [%(asctime)s]: %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

serverLogs = None
debugChannel = None

READY = False
preCache = []
userCache = []

startTime = int(time.time())

async def safe_send_message(channel, content=None, embeds=None):
    await channel.send(content, embed=embeds)

async def store_user(member, messages=0):
    db = mclient.fil.users
    roleList = []
    for role in member.roles:
        if role.id == member.guild.id:
            continue
        
        roleList.append(role.id)

    userData = {
        '_id': member.id,
        'messages': messages,
        'last_message': None,
        'roles': roleList,
        'punishments': []
    }
    db.insert_one(userData)

@bot.event
async def on_ready():
    global serverLogs
    global debugChannel
    global READY
    global userCache
    global preCache
    serverLogs = bot.get_channel(config.logChannel)
    debugChannel = bot.get_channel(config.debugChannel)
    db = mclient.fil.users
    logging.info('Bot has passed on_ready')

    if not READY:
        bot.load_extension('jishaku')
        bot.load_extension('cogs.moderation')
        bot.load_extension('cogs.utility')

        NS = bot.get_guild(238080556708003851)

        logging.info('[Cache] Performing initial database synchronization')
        guildCount = len(NS.members)
        userCount = 0
        for member in NS.members:
            userCount += 1
            await asyncio.sleep(0.01)
            logging.info(f'[Cache] Syncronizing user {userCount}/{guildCount}')
            doc = db.find_one({'_id': member.id})
            if not doc:
                await store_user(member)
                continue

            roleList = []
            for role in member.roles:
                roleList.append(role.id)

            if roleList == doc['roles']:
                continue

            db.update_one({'_id': member.id}, {'$set': {
                'roles': roleList
            }})

        logging.info('[Cache] Inital database syncronization complete')
        READY = True

        logging.info(f'Bot is fully initialized taking {int(time.time()) - startTime}')

@bot.event
async def on_resume():
    logging.warning('The bot has been resumed on Discord')

#@bot.event
#async def on_command_error(event, *args, **kwargs):
#    print(event)
#    print(args)
#    print(kwargs)

@bot.event
async def on_member_join(member):
    await bot.wait_until_ready()
    db = mclient.fil.users
    doc = db.find_one({'_id': member.id})
    roleList = []

    if not doc:
        restored = False
        await store_user(member)

    else:
        if doc['roles']:
            restored = True
            for x in doc['roles']:
                role = member.guild.get_role(x)
                if role:
                    roleList.append(role)
    
            await member.edit(roles=roleList, reason='Automatic role restore action')

        else:
            restored = False
        
    joinEmbed = discord.Embed(color=discord.Color(0x4f941e), description=f'User <@{member.id}> joined.', timestamp=datetime.datetime.utcnow())
    joinEmbed.set_author(name=f'User joined | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
    await safe_send_message(serverLogs, embeds=joinEmbed)

    if restored:
        roleText = ''
        for z in roleList:
            roleText += f'{z}, '

        restoreEmbed = discord.Embed(color=discord.Color(0x25a5ef), description=f'<@{member.id}>\'s previous roles have been restored', timestamp=datetime.datetime.utcnow())
        restoreEmbed.set_author(name=f'User restored | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
        restoreEmbed.add_field(name='Restored roles', value=roleText[:-2])
        await safe_send_message(serverLogs, embeds=restoreEmbed)

@bot.event
async def on_member_remove(member):
    await bot.wait_until_ready()
    embed = discord.Embed(color=discord.Color(0x772F30), description=f'User <@{member.id}> left.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'User left | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
    await safe_send_message(serverLogs, embeds=embed)

@bot.event
async def on_message(message):
    await bot.wait_until_ready()
    if message.author.bot:
        return
    
    if message.channel.type != discord.ChannelType.text:
        logging.error(f'Discarding bad message {message.channel.type}')
        return
    
    while not READY: # We need on_ready tasks to complete prior to handling
        logging.debug(f'Not READY. Delaying message {message.id}')
        await asyncio.sleep(1)

    db = mclient.fil.users
    doc = db.find_one_and_update({'_id': message.author.id}, {'$inc': {'messages': 1}, '$set': {'last_message': int(time.time())}})
    if not doc:
        await store_user(message.author, 1)

    await bot.process_commands(message) # Continue commands

@bot.event
async def on_message_delete(message):
    await bot.wait_until_ready()
    if message.type != discord.MessageType.default:
        return # No system messages

    if not message.content:
        return # Blank or null content (could be embed)

    # Discord allows 1024 chars per embed field value, but a message can have 2000 chars
    content = message.content if len(message.content) < 1000 else message.content[:1000] + '...'

    embed = discord.Embed(color=discord.Color(0xff6661), description=f'Message by <@{message.author.id}> in <#{message.channel.id}> was deleted.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'Message deleted | {message.author.name}#{message.author.discriminator}')
    embed.add_field(name='Message', value=content)
    await safe_send_message(serverLogs, embeds=embed)

@bot.event
async def on_message_edit(before, after):
    await bot.wait_until_ready()
    if before.content == after.content:
        return

    if before.type != discord.MessageType.default:
        return # No system messages

    if not after.content or not before.content:
        return # Blank or null content (could be embed)
    
    # Discord allows 1024 chars per embed field value, but a message can have 2000 chars
    before_content = before.content if len(before.content) < 1000 else before.content[:1000] + '...'
    after_content = after.content if len(after.content) < 1000 else after.content[:1000] + '...'
    
    embed = discord.Embed(color=discord.Color(0x25a5ef), description=f'Message by <@{before.author.id}> in <#{before.channel.id}> was edited.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'Message edited | {before.author.name}#{before.author.discriminator}')
    embed.add_field(name='Before', value=before_content, inline=True)
    embed.add_field(name='After', value=after_content, inline=True)
    await safe_send_message(serverLogs, embeds=embed)

@bot.command()
async def ping(ctx):
    return await ctx.send('Pong!')

@bot.command()
@commands.is_owner()
async def reload(ctx, module):
    await bot.wait_until_ready()
    try:
        logging.info(f'Attempting to reload extension {module}')
        bot.reload_extension(f'cogs.{module}')
    except discord.ext.commands.errors.ExtensionNotLoaded:
        logging.error(f'Error while reloading extension {module}')
        return await ctx.send(':x: The provided module is not loaded')
    
    await ctx.send(':heavy_check_mark: Module reloaded successfully')

@reload.error
async def reload_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        logging.error('We handled bois')
        return
    
    logging.error('Oopsie, cannot handle all this erroring')

@bot.command()
@commands.is_owner()
async def update(ctx, sub, *args):
    await bot.wait_until_ready()
    if sub == 'pfp':
        if not ctx.message.attachments:
            return await ctx.send(':warning: An attachment to change the picture to was not provided')
        
        else:
            attachment = await ctx.message.attachments[0].read()
            await bot.user.edit(avatar=attachment)

        return await ctx.send('Done.')

    elif sub == 'name':
        username = ''
        for x in args:
            username += f'{x} '

        if len(username[:-1]) >= 32:
            return await ctx.send(':warning: That username is too long.')

        await bot.user.edit(username=username)

    else:
        return await ctx.send('Invalid sub command')

print('\033[94mFils-A-Mech python by MattBSG#8888 2019\033[0m')
bot.run(config.token)
