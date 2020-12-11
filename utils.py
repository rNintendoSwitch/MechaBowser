import typing
import datetime
import time
import uuid
import logging
import re

import discord
import pymongo

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

linkRe = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[#-_]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

archiveHeader = '# Message archive for guild "{0.name}" ({0.id})\nIncluded channels: {1}\n# Format:\n[date + time] Member ID/Message ID/Channel/Username - Message content\n----------------\n'
timeUnits = {
    's': lambda v: v,
    'm': lambda v: v * 60,
    'h': lambda v: v * 60 * 60,
    'd': lambda v: v * 60 * 60 * 24,
    'w': lambda v: v * 60 * 60 * 24 * 7,
}

# Most NintenDeals code (decommissioned 4/25/2020) was removed on 12/09/2020
# https://github.com/rNintendoSwitch/MechaBowser/commit/d1550f1f4951c35ca953e1ceacaae054fc9d4963

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

async def issue_pun(user, moderator, _type, reason=None, expiry=None, active=True, context=None, _date=None, public=True):
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
        'public': public,
        'public_log_message': None,
        'public_log_channel': None
    })
    return docID

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

async def send_modlog(bot, channel, _type, footer, reason, user=None, username=None, userid=None, moderator=None, expires=None, extra_author='', timestamp=None, public=False, delay=300):
    if user: # Keep compatibility with sources without reliable user objects (i.e. ban), without forcing a long function every time
        username = str(user)
        userid = user.id

    author = f'{config.punStrs[_type]} '
    if extra_author:
        author += f'({extra_author}) '
    author += f'| {username} ({userid})'
    if not timestamp:
        timestamp = datetime.datetime.utcnow()

    embed = discord.Embed(color=config.punColors[_type], timestamp=timestamp)
    embed.set_author(name=author)
    embed.set_footer(text=footer)
    embed.add_field(name='User', value=f'<@!{userid}>', inline=True)
    if moderator:
        if not isinstance(moderator, str): # Convert to str
            moderator = moderator.mention

        embed.add_field(name='Moderator', value=moderator, inline=True)

    if expires:
        embed.add_field(name='Expires', value=expires)
    embed.add_field(name='Reason', value=reason)

    await channel.send(embed=embed)
    if public:
        event_loop = bot.loop
        post_action = event_loop.call_later(delay, event_loop.create_task, send_public_modlog(bot, footer, bot.get_channel(config.publicModChannel), expires))
        return post_action

async def send_public_modlog(bot, id, channel, expires=None):
    db = mclient.bowser.puns
    doc = db.find_one({'_id': id})
    user = await bot.fetch_user(doc["user"])

    author = f'{config.punStrs[doc["type"]]} '
    if doc['type'] == 'blacklist':
        author += f'({doc["context"]}) '
    author += f'| {user} ({user.id})'

    embed = discord.Embed(color=config.punColors[doc['type']], timestamp=datetime.datetime.utcfromtimestamp(doc['timestamp']))
    embed.set_author(name=author)
    embed.set_footer(text=id)
    embed.add_field(name='User', value=user.mention, inline=True)
    if expires:
        embed.add_field(name='Expires', value=expires)
    if doc['sensitive']:
        embed.add_field(name='Reason', value='This action\'s reason has been marked sensitive by the moderation team and is hidden. See <#671003325495509012> for more information on why logs are marked sensitive')
    elif doc['context'] == 'vote': # Warning review
        embed.add_field(name='Reason', value='A moderator has reviewed a previous warning and reduced it by one level')
    else:
        embed.add_field(name='Reason', value=doc['reason'])

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

def spans_overlap_link(string: str, spans: typing.List[typing.Tuple[int, int]]) -> typing.List[bool]:
    '''
    Returns list of booleans for every character span passed (as `(start, end)`) if they overlap a link in given string.
    '''
    START, END = (0, 1) # Consts for readablity of (start, end) tuples

    links = linkRe.finditer(string)

    if not spans: return []
    if not links: return [False] * len(spans)

    link_spans = list(map(lambda m: m.span(), links))
    overlaps = [False] * len(spans)

    for i, span in enumerate(spans):
        for link in link_spans:
            # If span overlaps with link (https://nedbatchelder.com/blog/201310/range_overlap_in_two_compares.html)
            if span[END] >= link[START] and link[END] >= span[START]:
                overlaps[i] = True
                break

    return overlaps

def re_match_nonlink(pattern: typing.Pattern, string: str) -> typing.Optional[bool]:
    '''
    Returns if any regex match for given pattern in string does not overlap a link.

    Returns:
    True  - At least one non link-overlapping match was found.
    False - All matches overlapped a link.
    None  - No match found, regardless of link overlap.
    '''
    matches = list(re.finditer(pattern, string))
    spans = list(map(lambda m: m.span(), matches))

    if not matches: return None

    overlaps = spans_overlap_link(string, spans)
    return any(not overlap for overlap in overlaps)

# TODO: Look into replacing _stats_roles() and its embed_paginate(): using chunks instead of fields-- another function to prep for this one?
async def send_paginated_embed(channel: discord.TextChannel,
                               fields: typing.List[typing.Dict], # name: str , value: str, inline: optional bool
                               *, 
                               owner: typing.Optional[discord.User] = None, 
                               timeout: int = 600,
                               title: typing.Optional[str] = '',
                               description: typing.Optional[str] = None,
                               colour: typing.Union[discord.Colour, int, None] = 0,
                               author: typing.Optional[typing.Dict] = None) -> discord.Message: # author = name: str, icon_url: optional str
    '''Displays an interactive paginated embed of given fields, with optional owner-locking, until timed out.'''

    PAGE_TEMPLATE = '(Page {0}/{1})'
    FOOTER_INSTRUCTION = 'Use ➡️ and ⬅️ to change pages and ⏹️ to end'
    FOOTER_LAST_ACTION = ['In use by {0}', 'Last used by {0}', 'Ended by {0}']
    FOOTER_TIME_STATUS = ['Expires in', 'Expired at', 'Ended at']

    # max(...) gets longest item; formats to mock discordtag#0000 (max length: 32+1+4=37)
    footer_max_length = len(' | '.join([max(FOOTER_LAST_ACTION, key=len), FOOTER_INSTRUCTION, max(FOOTER_TIME_STATUS, key=len)]).format('.'*37))
    title_max_length = len(title) + len(PAGE_TEMPLATE.format('99', '99')) + 1
    description_length = 0 if not description else len(description)
    author_length = 0 if not author else len(author['name'])

    page_char_cap = 6000 - footer_max_length - title_max_length - description_length - author_length

    baseEmbed = discord.Embed(description=None if not description else description, colour=colour)
    if author: baseEmbed.set_author(name=author['name'], icon_url=None if not 'icon_url' in author else author['icon_url'])

    # Build pages
    pages = []

    while fields:
        remaining_chars = page_char_cap
        page = []

        for field in fields.copy():
            field_length = len(field['name']) + len(field['value'])

            if remaining_chars - field_length < 0: break
            remaining_chars -= field_length
    
            page.append(fields.pop(0))

        pages.append(page)
    return pages


def setup(bot):
    logging.info('[Extension] Utils module loaded')

def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
