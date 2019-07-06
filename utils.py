import asyncio
import typing
import datetime
import time

import discord
import pymongo

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

archiveHeader = '# Message archive for "#{0.name}" ({0.id}) in guild "{1.name}" ({1.id})\n# Format:\n[date + time] Member ID/Message ID/Username - Message content\n----------------\n'

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
