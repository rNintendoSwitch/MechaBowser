import asyncio
import logging
import re
import typing
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

serverLogs = None
modLogs = None
SMM2LevelID = re.compile(r'([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})', re.I | re.M)
SMM2LevelPost = re.compile(r'Name: ?(.+)\n\n?(?:Level )?ID: ?([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})\n\n?Style: ?(.+)\n\n?(?:Theme: ?(.+)\n\n?)?(?:Tags: ?(.+)\n\n?)?Difficulty: ?(.+)\n\n?Description: ?(.+)', re.I)

class ChatFilter(commands.Cog, name='Chat Filter'):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.bot.wait_until_ready()

        if message.author.bot or message.type != discord.MessageType.default:
            return

        #Filter for #mario
        if message.channel.id == 595665653260615696: # #mario
            if re.search(SMM2LevelID, message.content):
                await message.delete()
                response = await message.channel.send(f'<:redTick:402505117733224448> <@{message.author.id}> Please do not post Super Mario Maker 2 level codes '\
                    'here. Post in <#595203237108252672> with the pinned template instead.')

                await response.delete(delay=20)
            return

        #Filter for #smm2-levels
        if message.channel.id == 595203237108252672:
            if not re.search(SMM2LevelID, message.content):
                # We only want to filter posts with a level id
                return

            block = re.search(SMM2LevelPost, message.content)
            if not block:
                # No match for a properly formatted level post
                response = await message.channel.send(f'<:redTick:402505117733224448> <@{message.author.id}> Your level is formatted incorrectly, please see the pinned messages for the format. A copy '\
                    f'of your message is included and will be deleted shortly. You can resubmit your level at any time.\n\n```{message.content}```')
                await message.delete()
                return await response.delete(delay=25)

            # Lets make this readable
            levelName = block.group(1)
            levelID = block.group(2)
            levelStyle = block.group(3)
            levelTheme = block.group(4)
            levelTags = block.group(5)
            levelDifficulty = block.group(6)
            levelDescription = block.group(7)

            embed = discord.Embed(color=discord.Color(0x6600FF))
            embed.set_author(name=str(message.author), icon_url=message.author.avatar_url)
            embed.add_field(name='Name', value=levelName, inline=True)
            embed.add_field(name='Level ID', value=levelID, inline=True)
            embed.add_field(name='Description', value=levelDescription, inline=False)
            embed.add_field(name='Style', value=levelStyle, inline=True)
            embed.add_field(name='Difficulty', value=levelDifficulty, inline=True)
            if levelTheme:
                embed.add_field(name='Theme', value=levelTheme, inline=False)
            if levelTags:
                embed.add_field(name='Tags', value=levelTags, inline=False)

            #await message.delete() Flap broke embedssssssss
            try:
                await message.channel.send(embed=embed)
                await message.delete()

            except discord.errors.Forbidden:
                # Fall back to leaving user text
                logging.error(f'[Filter] Unable to send embed to {message.channel.id}')
            return

def setup(bot):
    global serverLogs
    global modLogs
    global Client

    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)
    Client = bot

    bot.add_cog(ChatFilter(bot))
    bot.add_command(_info)

    logging.info('Utility module loaded')

def teardown(bot):
    bot.remove_cog('Chat Filter')
    logging.info('Utility module unloaded')