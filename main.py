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
activityStatus = discord.Activity(type=discord.ActivityType.playing, name='with Fils-A-Mech')
bot = commands.Bot('()', max_messages=30000, fetch_offline_members=True, activity=activityStatus)

LOG_FORMAT = '%(levelname)s [%(asctime)s]: %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

serverLogs = None
debugChannel = None

READY = False
preCache = []
userCache = []

async def safe_send_message(channel, content=None, embeds=None):
    await channel.send(content, embed=embeds)

@tasks.loop(seconds=15.0)
async def stats_update():
    global userCache
    global preCache
    logging.info('[Cache Tool] Running cache update')
    updateCache = []
    if not READY:
        logging.info('[Cache Tool] Bot not READY. Skipping cache check')
        return

    for item in userCache:
        if item not in preCache: # Something that is not the same
            logging.info(f"[Cache Tool] Updating user db for {item['_id']}")
            updateCache.append(item)
            db = mclient.fil.users
            doc = db.find_one({'_id': item['_id']})
            if not doc:
                logging.error(f"[Cache Tool] Unexpected user {item['_id']}in cache, creating")
                db.insert_one(item)
                continue

            db.update_one({'_id': item['_id']}, {'$set': item})
            preCache = copy.deepcopy(userCache)
            break

    logging.info('[Cache Tool] Done')

async def db_cache_merge(member, guild, data): # TODO Merge newEntry with dbEntry. They are now the same
    db = mclient.fil.users
    dbUser = db.find_one({'_id': member.id})

    warnTier1 = config.warnTier1
    warnTier2 = config.warnTier2
    warnTier3 = config.warnTier3
    muteRole = config.mute
    logging.debug(f'Evaluating starting cache for {member.id}')

    if not dbUser:
        # We don't have a record yet, make and toss it back
        dbEntry = {
            '_id': member.id,
            'messages': data['messages'],
            'last_message': data['last_message'],
            'roles': data['roles'],
            'punishments': data['punishments']
        }
        return data, dbEntry
    
    newEntry = {}
    newEntry['_id'] = member.id
    newEntry['messages'] = dbUser['messages']
    newEntry['last_message'] = dbUser['last_message']
    if Counter(dbUser['roles']) != Counter(data['roles']):
        db.update_one({'_id': member.id}, {'$set': {
            'roles': data['roles']
        }})
    
    newEntry['roles'] = data['roles']
    newEntry['punishments'] = dbUser['punishments']
    if not newEntry['punishments']:
        updateRequired = False
        for pun in newEntry['punishments']:
            if pun['active']:
                if pun['type'] == 'tier1' and warnTier1 not in newEntry['roles']:
                    updateRequired = True
                    newEntry['roles'].append(warnTier1)

                elif pun['type'] == 'tier2' and warnTier2 not in newEntry['roles']:
                    updateRequired = True
                    newEntry['roles'].append(warnTier2)

                elif pun['type'] == 'tier3' and warnTier3 not in newEntry['roles']:
                    updateRequired = True
                    newEntry['roles'].append(warnTier3)

                elif pun['type'] == 'mute' and muteRole not in newEntry['roles']:
                    updateRequired = True
                    newEntry['roles'].append(muteRole)

        if updateRequired:
            newRoles = []
            for role in newEntry['roles']:
                n = guild.get_role(role)
                if not n:
                    # discord.py failed to get role and returned None. Ignore it.
                    logging.error(f'Unable to get role with ID {role}')
                    continue
                
                newRoles.append(n)

            await member.edit(roles=newRoles)
        
        return newEntry, None

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
        stats_update.start() # TODO: Workaround list(dict()) not being hashable

        NS = bot.get_guild(238080556708003851)
        dbQueue = []

        logging.info('Starting cache population')
        async def cache_fetch_loop():
            guildSize = len(NS.members)
            memberNumber = 0
            for member in NS.members:
                memberNumber += 1
                logging.info(f'Populating member {memberNumber}/{guildSize} into cache')
                serverData = {
                    'messages': 0,
                    'last_message': None,
                    'roles': [x.id for x in member.roles],
                    'punishments': []
                    }
                cacheData, dbData = await db_cache_merge(member, NS, serverData)
                userCache.append(cacheData)
                if not dbData:
                    # This is an update
                    db.update_one({'_id': member.id}, {'$set': cacheData})
                dbQueue.append(dbData)
                await asyncio.sleep(0.01)

        await asyncio.gather(cache_fetch_loop())
        preCache = copy.deepcopy(userCache) # Initialize starting member cache
        #logging.info(f'start usercache {userCache}')
        #logging.info(f'start precache {preCache}')
        READY = True

    dbQueue = list(filter(None, dbQueue))
    if dbQueue: # New records to insert
        db.insert_many(dbQueue)

    logging.info('Bot is fully initialized')

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
    global userCache
    await bot.wait_until_ready()
    db = mclient.fil.users
    doc = db.find_one({'_id': member.id})
    roleList = []

    if not doc:
        restored = False
        for x in member.roles:
            if x.id == member.guild.id: # We don't want @everyone in the role list. Grab any roles immediately assigned before payload
                continue

            roleList.append(x.id)

        userData = {
            '_id': member.id,
            'messages': 0,
            'last_message': None,
            'roles': roleList,
            'punishments': []
        }
        userCache.append(userData)

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
    global userCache
    await bot.wait_until_ready()
    if message.author == bot.user:
        return
    
    if message.channel.type != discord.ChannelType.text:
        logging.error(f'Discarding bad message {message.channel.type}')
        return
    
    while not READY: # We need on_ready tasks to complete prior to handling
        logging.debug(f'Not READY. Delaying message {message.id}')
        await asyncio.sleep(1)
    for obj in userCache:
        if obj['_id'] == message.author.id:
            obj['messages'] += 1
            obj['last_message'] = int(time.time())
            break

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
