import asyncio
import typing
import datetime
import time
import uuid
import logging

import discord
import pymongo

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

archiveHeader = '# Message archive for "#{0.name}" ({0.id}) in guild "{1.name}" ({1.id})\n# Format:\n[date + time] Member ID/Message ID/Username - Message content\n----------------\n'
timeUnits = {
    's': lambda v: v,
    'm': lambda v: v * 60,
    'h': lambda v: v * 60 * 60,
    'd': lambda v: v * 60 * 60 * 24,
    'w': lambda v: v * 60 * 60 * 24 * 7,
}

async def message_archive(archive: typing.Union[discord.Message, list], edit=None):
    db = mclient.bowser.archive
    if type(archive) != list:
        # Single message to archive
        archive = [archive]

    body = archiveHeader.format(archive[0].channel, archive[0].guild)
    archiveID = f'{archive[0].id}-{int(time.time() * 1000)}'
    messageIDs = []

    if edit:
        msgBefore = archive[0]
        msgAfter = archive[1]

        body += f'[{msgBefore.created_at.strftime("%Y/%m/%d %H:%M:%S UTC")}] ({msgBefore.author.id}/{msgBefore.id}/{str(msgBefore.author)}): message edit:\n'
        body += f'--- Before ---\n{msgBefore.content}\n\n--- After ---\n{msgAfter.content}'

    else:
        channels = []
        for msg in archive: # TODO: attachment CDN urls should be posted as message
            if not msg.channel not in channels: channels.append(msg.channel)
            messageIDs.append(msg.id)
            content = '*No message content could be saved, could be embed or attachment*' if not msg.content else msg.content
            body += f'[{msg.created_at.strftime("%Y/%m/%d %H:%M:%S UTC")}] ({msg.author.id}/{msg.id}/{str(msg.author)}): {content}\n'

    db.insert_one({
        '_id': archiveID,
        'body': body,
        'messages': messageIDs,
        'timestamp': int(time.time())

    })
    return archiveID

async def store_user(member, messages=0):
    db = mclient.bowser.users
    # Double check exists
    if db.find_one({'_id': member.id}):
        logging.error('Attempted to store user that already exists!')
        return

    roleList = []
    for role in member.roles:
        if role.id == member.guild.id:
            continue
        
        roleList.append(role.id)

    userData = {
        '_id': member.id,
        'roles': roleList
    }
    db.insert_one(userData)

async def issue_pun(user, moderator, _type, reason=None, expiry=None, active=True, context=None):
    db = mclient.bowser.puns
    timestamp = int(time.time())
    docID = str(uuid.uuid4())
    while db.find_one({'_id': docID}): # Uh oh, duplicate uuid generated
        docID = str(uuid.uuid4())

    db.insert_one({
        '_id': docID,
        'user': user,
        'moderator': moderator,
        'type': _type,
        'timestamp': timestamp,
        'reason': reason,
        'expiry': expiry,
        'context': context,
        'active': active
    })

async def resolve_duration(data):
    '''
    Takes a raw input string formatted 1w1d1h1m1s (any order)
    and converts to timedelta
    Credit https://github.com/b1naryth1ef/rowboat via MIT license
    '''
    value = 0
    digits = ''

    for char in data:
        if char.isdigit():
            digits += char
            continue

        if char not in timeUnits or not digits:
            raise KeyError('Time format not a valid entry')

        value += timeUnits[char](int(digits))
        digits = ''

    return datetime.datetime.utcnow() + datetime.timedelta(seconds=value + 1)

def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
