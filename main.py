import asyncio
import logging
import datetime

import pymongo
import discord
from discord.ext import commands

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)
bot = commands.Bot('.', max_messages=30000, fetch_offline_members=True)

LOG_FORMAT = '%(levelname)s [%(asctime)s]: %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.DEBUG)

serverLogs = None
modLogs = None

async def safe_send_message(channel, content=None, embeds=None):
    await channel.send(content, embed=embeds)

@bot.event
async def on_ready():
    global serverLogs
    global modLogs
    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)
    logging.warning('Bot has passed on_ready')

@bot.event
async def on_resume():
    logging.warning('The bot has been resumed on Discord')

@bot.event
async def on_member_join(member):
    embed = discord.Embed(color=discord.Color(0x4f941e), description=f'User <@{member.id}> joined.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'User joined | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
    await safe_send_message(serverLogs, embeds=embed)

@bot.event
async def on_member_remove(member):
    embed = discord.Embed(color=discord.Color(0x772F30), description=f'User <@{member.id}> left.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'User left | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
    await safe_send_message(serverLogs, embeds=embed)

@bot.event
async def on_message_delete(message):
    embed = discord.Embed(color=discord.Color(0xff6661), description=f'Message by <@{message.author.id}> in <#{message.channel.id}> was deleted.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'Message deleted | {message.author.name}#{message.author.discriminator}')
    embed.add_field(name='Message', value=message.content)
    await safe_send_message(serverLogs, embeds=embed)

@bot.event
async def on_message_edit(before, after):
    if before.content == after.content:
        return
    
    # Discord allows 1024 chars per embed field value, but a message can have 2000 chars
    before_content = before.content if len(before.content) < 1000 else before.content[:1000] + '...'
    after_content = after.content if len(after.content) < 1000 else after.content[:1000] + '...'
    
    embed = discord.Embed(color=discord.Color(0x25a5ef), description=f'Message by <@{before.author.id}> in <#{before.channel.id}> was edited.', timestamp=datetime.datetime.utcnow())
    embed.set_author(name=f'Message edited | {before.author.name}#{before.author.discriminator}')
    embed.add_field(name='Before', value=before_content, inline=True)
    embed.add_field(name='After', value=after_content, inline=True)
    await safe_send_message(serverLogs, embeds=embed)

@bot.command()
@commands.is_owner()
async def reload(ctx, module):
    try:
        bot.reload_extension(f'cogs.{module}')
    except discord.ext.commands.errors.ExtensionNotLoaded:
        return await ctx.send(':x: The provided module is not loaded')
    
    await ctx.send(':heavy_check_mark: Module reloaded successfully')

print('\033[94mFils-A-Mech python by MattBSG#8888 2019\033[0m')
bot.load_extension('cogs.moderation')
bot.run(config.token)
