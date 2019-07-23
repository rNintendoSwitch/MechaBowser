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

def resolve_duration(data):
    '''
    Takes a raw input string formatted 1w1d1h1m1s (any order)
    and converts to timedelta
    Credit https://github.com/b1naryth1ef/rowboat via MIT license

    data: str
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

    return datetime.datetime.utcnow() + datetime.timedelta(seconds=value)

def humanize_duration(duration):
    '''
    Takes a datetime object and returns a prettified
    weeks, days, hours, minutes, seconds string output
    Credit https://github.com/ThaTiemsz/jetski via MIT license

    duration: datetime.datetime
    '''
    now = datetime.datetime.utcnow()
    if isinstance(duration, datetime.timedelta):
        if duration.total_seconds() > 0:
            duration = datetime.datetime.today() + duration
        else:
            duration = datetime.datetime.utcnow() - datetime.timedelta(seconds=duration.total_seconds())
    diff_delta = duration - now
    diff = int(diff_delta.total_seconds())

    minutes, seconds = divmod(diff, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    weeks, days = divmod(days, 7)
    units = [weeks, days, hours, minutes, seconds]

    unit_strs = ['week', 'day', 'hour', 'minute', 'second']

    expires = []
    for x in range(0, 5):
        if units[x] == 0:
            continue
        else:
            if units[x] > 1:
                expires.append('{} {}s'.format(units[x], unit_strs[x]))
            else:
                expires.append('{} {}'.format(units[x], unit_strs[x]))
    
    return ', '.join(expires)

async def mod_cmd_invoke_delete(channel):
    print(channel.id)
    if channel.id in config.showModCTX:
        print('no invoke delete')
        return False

    else:
        print('invoke delete')
        return True


def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
