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