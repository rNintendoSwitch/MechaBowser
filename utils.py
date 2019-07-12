import asyncio
import typing
import datetime
import time
import uuid

import discord
import pymongo

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

archiveHeader = '# Message archive for "#{0.name}" ({0.id}) in guild "{1.name}" ({1.id})\n# Format:\n[date + time] Member ID/Message ID/Username - Message content\n----------------\n'

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
        for msg in reversed(archive): # TODO: attachment CDN urls should be posted as message
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

async def issue_pun(user, moderator, _type, reason=None, timestamp=int(time.time()), expiry=None, active=True, old_doc=None):
    db = mclient.bowser.puns
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
        'active': active
    })

def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
