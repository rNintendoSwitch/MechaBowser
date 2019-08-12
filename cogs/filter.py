import asyncio
import logging
import os

import pymongo
import dialogflow_v2 as dialogflow
import discord
from discord.ext import commands
from google.cloud import storage

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class AIFilter(commands.Cog):
    def __init__(self, bot):
        self.storage_client = storage.Client.from_service_account_json('service_account.json')
        self.bot = bot
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/mecha-bowser/service_account.json"

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.content: return
        if message.author.bot: return

        session_client = dialogflow.SessionsClient()
        session = session_client.session_path('newagent-nigrgq', 1)

        text = dialogflow.types.QueryInput(text=message.content)
        query = dialogflow.types.QueryInput(text=text)
        response = session_client.detect_intent(query_input=query)
        print('Detected intent: {} (confidence: {})\n'.format(
            response.query_result.intent.display_name,
            response.query_result.intent_detection_confidence))

def setup(bot):
    bot.add_cog(AIFilter(bot))
    logging.info('[Extension] Filter module loaded')

def teardown(bot):
    bot.remove_cog('AIFilter')
    logging.info('[Extension] Filter module unloaded')