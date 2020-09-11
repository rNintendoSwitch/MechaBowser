import asyncio
import typing
import datetime
import time
import uuid
import logging
import re

import discord
import pymongo
import gridfs
import requests
from selenium import webdriver
from bs4 import BeautifulSoup as bs

import config

driver = None
storePageRe = re.compile(r'(http[s]?:\/\/(?:[^.]*\.)?[^.]*\.[^\/]*)(?:.*)')
imageTagRe = re.compile(r'(?:src="([^"]*)")')
mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

archiveHeader = '# Message archive for guild "{0.name}" ({0.id})\nIncluded channels: {1}\n# Format:\n[date + time] Member ID/Message ID/Channel/Username - Message content\n----------------\n'
timeUnits = {
    's': lambda v: v,
    'm': lambda v: v * 60,
    'h': lambda v: v * 60 * 60,
    'd': lambda v: v * 60 * 60 * 24,
    'w': lambda v: v * 60 * 60 * 24 * 7,
}

async def message_archive(archive: typing.Union[discord.Message, list], edit=None):
    db = mclient.modmail.logs
    if type(archive) != list:
        # Single message to archive
        archive = [archive]

    archiveID = f'{archive[0].id}-{int(time.time() * 1000)}'
    if edit:
        db.insert_one({
            '_id': archiveID,
            'key': archiveID,
            'open': False,
            'created_at': str(archive[0].created_at),
            'closed_at': str(archive[0].created_at),
            'channel_id': str(archive[0].channel.id),
            'guild_id': str(archive[0].guild.id),
            'bot_id': str(config.parakarry),
            'recipient': {
                'id': 0,
                'name': archive[0].author.name,
                'discriminator': archive[0].author.discriminator,
                'avatar_url': str(archive[0].author.avatar_url_as(static_format='png', size=1024)),
                'mod': False
            },
            'creator': {
                'id': str(archive[0].author.id),
                'name': archive[0].author.name,
                'discriminator': archive[0].author.discriminator,
                'avatar_url': '',
                'mod': False
            },
            'closer': {
                'id': str(0),
                'name': 'message edited',
                'discriminator': 0,
                'avatar_url': ''
            },
            'messages': [
                {
                    'timestamp': str(archive[0].created_at),
                    'message_id': str(archive[0].id),
                    'content': archive[0].content,
                    'type': 'edit_before',
                    'author': {
                        'id': str(archive[0].author.id),
                        'name': archive[0].author.name,
                        'discriminator': archive[0].author.discriminator,
                        'avatar_url': str(archive[0].author.avatar_url_as(static_format='png', size=1024)),
                        'mod': False
                    },
                    'attachments': [x.url for x in archive[0].attachments]
                },
                {
                    'timestamp': str(archive[1].created_at),
                    'message_id': str(archive[1].id),
                    'content': archive[1].content,
                    'type': 'edit_after',
                    'author': {
                        'id': str(archive[1].author.id),
                        'name': archive[1].author.name,
                        'discriminator': archive[1].author.discriminator,
                        'avatar_url': str(archive[1].author.avatar_url_as(static_format='png', size=1024)),
                        'mod': False
                    },
                    'attachments': [x.url for x in archive[1].attachments]
                }
            ]
        })
        
    else:
        messages = []
        for msg in archive: # TODO: attachment CDN urls should be posted as message
            messages.append({
                'timestamp': str(msg.created_at),
                'message_id': str(msg.id),
                'content': msg.content if msg.content else '',
                'type': 'thread_message',
                'author': {
                    'id': str(msg.author.id),
                    'name': msg.author.name,
                    'discriminator': msg.author.discriminator,
                    'avatar_url': str(msg.author.avatar_url_as(static_format='png', size=1024)),
                    'mod': False
                },
                'channel': {
                    'id': str(msg.channel.id),
                    'name': msg.channel.name
                },
                'attachments': [x.url for x in msg.attachments]
            })

        db.insert_one({
            '_id': archiveID,
            'key': archiveID,
            'open': False,
            'created_at': str(archive[0].created_at),
            'closed_at': str(archive[0].created_at),
            'channel_id': str(archive[0].channel.id),
            'guild_id': str(archive[0].guild.id),
            'bot_id': str(config.parakarry),
            'recipient': {
                'id': 0,
                'name': '',
                'discriminator': 0,
                'avatar_url': 'https://cdn.discordapp.com/attachments/276036563866091521/695443024955834438/unknown.png',
                'mod': False
            },
            'creator': {
                'id': str(archive[0].author.id),
                'name': archive[0].author.name,
                'discriminator': archive[0].author.discriminator,
                'avatar_url': '',
                'mod': False
            },
            'closer': {
                'id': str(0),
                'name': 'message edited',
                'discriminator': 0,
                'avatar_url': ''
            },
            'messages': messages
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
	    'roles': roleList,
	    'joins': [(datetime.datetime.utcnow() - datetime.datetime.utcfromtimestamp(0)).total_seconds()],
	    'leaves': [],
        'lockdown': False,
        'jailed': False,
        'friendcode': None,
        'timezone': None,
        'modmail': True,
        'trophies': [],
        'trophyPreference': [],
        'favgames': [],
        'regionFlag': None,
        'profileSetup': False,
        'background': 'default',
        'backgrounds': ['default']
    }
    db.insert_one(userData)

async def issue_pun(user, moderator, _type, reason=None, expiry=None, active=True, context=None, _date=None):
    db = mclient.bowser.puns
    timestamp = time.time() if not _date else _date
    docID = str(uuid.uuid4())
    while db.find_one({'_id': docID}): # Uh oh, duplicate uuid generated
        docID = str(uuid.uuid4())

    db.insert_one({
        '_id': docID,
        'user': user,
        'moderator': moderator,
        'type': _type,
        'timestamp': int(timestamp),
        'reason': reason,
        'expiry': expiry,
        'context': context,
        'active': active,
        'sensitive': False,
        'public_log_message': None,
        'public_log_channel': None
    })
    return docID

async def _request_noa(nsuid):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Origin': 'https://www.nintendo.com',
        'X-Algolia-API-Key': '9a20c93440cf63cf1a7008d75f7438bf',
        'X-Algolia-Application-ID': 'U3B6GR4UA3',
        'Host': 'u3b6gr4ua3-dsn.algolia.net',
        'Referer': 'https://www.nintendo.com/pos-redirect/{}?a=gdp'.format(nsuid)
    }
    body = {
        "requests": [
            {
                "indexName": "noa_aem_game_en_us",
                "params": "query={}&hitsPerPage=1&maxValuesPerFacet=30&page=0".format(nsuid),
                "facetFilters": [["platform:Nintendo Switch"]]
            }
        ]
    }
    algolia = requests.post('https://u3b6gr4ua3-dsn.algolia.net/1/indexes/*/queries', headers=headers, json=body)
    try:
        algolia.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    response = algolia.json()
    #print(response)
    if not response['results'][0]['hits']:
        raise KeyError('_noa game not found based on id')

    game = response['results'][0]['hits'][0]
    if not game:
        pass # TODO: fill in title for slug and web request

    description = discord.utils.escape_markdown(game['description']).replace(' \n      ', '').replace('    ', '').replace('\n\n', '\n')
    infoDict['description'] = re.sub(r'([,!?.:;])(?![\\n ])', '\g<1>', description)
    infoDict['category'] = ', '.join(game['categories'])
    infoDict['publisher'] = None if not game['publishers'] else ' & '.join(game['publishers'])
    infoDict['developer'] = None if not game['developers'] else ' & '.join(game['developers'])
    infoDict['image'] = 'https://nintendo.com' + game['boxArt']

    return infoDict

async def _request_noe(title):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0'
    }

    search = requests.get('https://search.nintendo-europe.com/en/select?q="{}"&start=0&rows=4000&wt=json&sort=title asc&fq=type:GAME AND system_names_txt:"switch"'.format(title), headers=headers)
    try:
        search.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    response = search.json()
    #print(response)
    gameList = response['response']['docs']
    game = None

    for entry in gameList:
        if entry['title'] == title:
            game = entry
            break

    if not game:
        raise KeyError('Game {} not found in returned NOE search array'.format(title))

    infoDict['description'] = None if not game['excerpt'] else game['excerpt']
    infoDict['category'] = ', '.join(game['pretty_game_categories_txt'])
    infoDict['publisher'] = None if not game['publisher'] else game['publisher']
    infoDict['developer'] = None if not game['developer'] else game['developer']
    infoDict['image'] = 'https:' + game['image_url']

    return infoDict


async def _request_noj(nsuid):
    infoDict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0'
    }

    titleAPI = requests.get('https://ec.nintendo.com/api/JP/ja/related/title/{}'.format(nsuid), headers=headers)
    try:
        titleAPI.raise_for_status()

    except Exception as e:
        raise RuntimeError(e)

    gameDesc = titleAPI.json()
    #print(gameDesc)
    response = gameDesc['related_informations']['related_information'][0]

    infoDict['description'] = None if not response['description'] else response['description']
    infoDict['image'] = response['image_url']
    # TODO: Scrape nintendo.co.jp entries for extra details
    infoDict['category'] = None
    infoDict['publisher'] = None
    infoDict['developer'] = None

    return infoDict


async def game_data(nxid, desc_cap=2048):
    db = mclient.bowser.games
    fs = gridfs.GridFS(mclient.bowser)
    gameDoc = db.find_one({'_id': nxid})

    if gameDoc['description'] and gameDoc['cacheUpdate'] > (time.time() - 86400 * 30): # Younger than 30 days
        return gameDoc

    titles = gameDoc['titles']
    print(titles)

    if titles['NA']:
        infoDict = await _request_noa(gameDoc['nsuids']['NA'])

    elif titles['EU']:
        infoDict = await _request_noe(gameDoc['titles']['EU'].encode('latin-1', errors='replace'))

    else:
        infoDict = await _request_noj(gameDoc['nsuids']['JP'])

    if len(infoDict['description']) > desc_cap: infoDict['description'] = f'{infoDict["description"][:desc_cap - 3]}...'

    if not fs.exists(nxid) or gameDoc['cacheUpdate'] < (time.time() - 86400 * 30): # Image not stored or older than 30 days
        if fs.exists(nxid): fs.delete(nxid)
        r = requests.get(infoDict['image'], stream=True)
        if r.status_code != 200:
            raise RuntimeError(f'Nintendo returned non-200 status code {r.status_code}')

        fs.put(r.raw, _id=nxid)

    infoDict['size'] = None # Depreciated
    db.update_one({'_id': nxid}, {'$set': {
        'description': infoDict['description'],
        'category': infoDict['category'],
        'publisher': infoDict['publisher'],
        'developer': infoDict['developer'],
        'size': infoDict['size'],
        'image': infoDict['image'],
        'cacheUpdate': int(time.time())
        }})

    return infoDict

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

    return datetime.datetime.utcnow() + datetime.timedelta(seconds=value + 1)

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
	
    if diff < 0:
      diff = -diff
      ago = ' ago'
    else: ago = ''
	
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
            if units[x] < -1 or units[x] > 1:
                expires.append('{} {}s'.format(units[x], unit_strs[x]))

            else:
                expires.append('{} {}'.format(units[x], unit_strs[x]))
    
    if not expires: return '0 seconds'
    return ', '.join(expires) + ago

async def mod_cmd_invoke_delete(channel):
    if channel.id in config.showModCTX or channel.category_id in config.showModCTX:
        return False

    else:
        return True

async def embed_paginate(chunks: list, page=1, header=None, codeblock=True):
    if page <= 0: raise IndexError('Requested page cannot be less than one')
    charLimit = 2048 if not codeblock else 2042 # 2048 - 6 for 6 backticks
    pages = 1
    requestedPage = ''

    if not header:
        text = ''

    else:
        text = header

    if codeblock:
        header = '```' if not header else header + '```'
        text = header

    for x in chunks:
        if len(x) > charLimit:
            raise IndexError('Individual chunk surpassed character limit')

        if len(text) + len(x) > charLimit:
            if pages == page:
                requestedPage = text if not codeblock else text + '```'

            text = header + x if header else x
            pages += 1
            continue

        text += x

    if page > pages:
        raise IndexError('Requested page out of range')

    if pages == 1:
        requestedPage = text if not codeblock else text + '```'

    return requestedPage, pages

async def send_modlog(bot, channel, _type, footer, reason, user=None, username=None, userid=None, moderator=None, expires=None, extra_author='', timestamp=datetime.datetime.utcnow(), public=False, delay=5):
    if user: # Keep compatibility with sources without reliable user objects (i.e. ban), without forcing a long function every time
        username = str(user)
        userid = user.id

    author = f'{config.punStrs[_type]} '
    if extra_author:
        author += f'({extra_author}) '
    author += f'| {username} ({userid})'

    embed = discord.Embed(color=config.punColors[_type], timestamp=timestamp)
    embed.set_author(name=author)
    embed.set_footer(text=footer)
    embed.add_field(name='User', value=f'<@!{userid}>', inline=True)
    if moderator:
        embed.add_field(name='Moderator', value=moderator.mention, inline=True)
    if expires:
        embed.add_field(name='Expires', value=expires)
    embed.add_field(name='Reason', value=reason)

    await channel.send(embed=embed)
    if public:
        event_loop = bot.loop
        post_action = event_loop.call_later(delay, event_loop.create_task, send_public_modlog(bot, footer, bot.get_channel(752224051153469594), expires))
        return post_action

async def send_public_modlog(bot, id, channel, expires=None):
    db = mclient.bowser.puns
    doc = db.find_one({'_id': id})
    user = await bot.fetch_user(doc["user"])

    embed = discord.Embed(color=config.punColors[doc['type']], timestamp=datetime.datetime.utcfromtimestamp(doc['timestamp']))
    embed.set_author(name=f'{config.punStrs[doc["type"]]} | {user} ({user.id})')
    embed.set_footer(text=id)
    embed.add_field(name='User', value=user.mention, inline=True)
    if expires:
        embed.add_field(name='Expires', value=expires)
    embed.add_field(name='Reason', value=doc['reason'] if not doc['sensitive'] else 'This action\'s reason has been marked sensitive by the moderation team and is hidden. See <#671003325495509012> for more information on why logs are marked sensitive')

    if doc['moderator'] == bot.user.id:
        embed.description = 'This is an automatic action'

    message = await channel.send(embed=embed)
    db.update_one({'_id': id}, {'$set': {
        'public_log_message': message.id,
        'public_log_channel': channel.id
    }})

def format_pundm(_type, reason, moderator, details=None, auto=False):
    infoStrs = {
        'warn': f'You have been **warned (now {details})** on',
        'warnup': f'Your **warning level** has been **increased (now {details})** on',
        'warndown': f'Your **warning level** has been **decreased (now {details})** on',
        'warnclear': f'Your **warning** has been **cleared** on',
        'mute': f'You have been **muted ({details})** on',
        'unmute': f'Your **mute** has been **removed** on',
        'blacklist': f'Your **posting permissions** have been **restricted** in {details} on',
        'unblacklist': f'Your **posting permissions** have been **restored** in {details} on',
        'kick': 'You have been **kicked** from',
        'ban': 'You have been **banned** from',
        'automod-word': 'You have violated the word filter on'
    }
    mod = f'{moderator} ({moderator.mention})' if not auto else 'Automatic action'

    punDM = infoStrs[_type] + f' the /r/NintendoSwitch Discord server.\n'
    punDM += f'Reason:```{reason}```'
    punDM += f'Responsible moderator: {mod}\n\n'
    if details == 'modmail':
        punDM += 'If you have questions concerning this matter, please feel free to contact the moderator that took this action or another member of the moderation team.\n'

    elif _type == 'ban':
        punDM += f'If you would like to appeal this ban, you may join our ban appeal server to dispute it with the moderation team: {config.banAppealInvite}\n'

    else:
        punDM += f'If you have questions concerning this matter you may contact the moderation team by sending a DM to our modmail bot, Parakarry (<@{config.parakarry}>).\n'

    punDM += 'Please do not respond to this message, I cannot reply.'

    return punDM

def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
