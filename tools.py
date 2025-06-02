import asyncio
import logging
import os
import re
import time
import typing
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

import config
import discord
import pymongo


mclient = pymongo.MongoClient(config.mongoURI)

linkRe = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[#-_]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', re.I)
reasonFilterLinkRe = re.compile(
    r'http[s]?://[a-zA-Z0-9#-_!*\(\),]+|(?<=\b)[a-zA-Z0-9.-]*\.[a-zA-Z0-9.-]+\/[a-zA-Z0-9#-_!*\(\),]*', re.I
)
reasonFilterWhitelistedDomains = [
    'rnintendoswitch.com',
    'discord.gg',
    'discord.me',
    'discord.com',
    'discordapp.com',
    'tenor.com',
]

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


class RiskyConfirmation(discord.ui.View):
    message: discord.Message | None = None

    def __init__(self, timeout=120.0):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.disable_buttons()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='No', style=discord.ButtonStyle.primary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.disable_buttons()
        await interaction.response.edit_message(view=self)
        self.stop()

    def disable_buttons(self):
        for c in self.children:
            c.disabled = True

    async def on_timeout(self):
        self.disable_buttons()
        if self.message:
            await self.message.edit(view=self)


class NormalConfirmation(discord.ui.View):
    message: discord.Message | None = None

    def __init__(self, timeout=120.0):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.edit_message(view=None)
        self.stop()

    @discord.ui.button(label='No', style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.edit_message(view=None)
        self.stop()

    async def on_timeout(self):
        if self.message:
            await self.message.edit(view=None)


async def message_archive(archive: typing.Union[discord.Message, list], edit=None):
    db = mclient.modmail.logs
    if type(archive) != list:
        # Single message to archive
        archive = [archive]

    archiveID = f'{archive[0].id}-{int(time.time() * 1000)}'
    if edit:
        db.insert_one(
            {
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
                    'avatar_url': archive[0].author.display_avatar.with_format('png').with_size(1024).url,
                    'mod': False,
                },
                'creator': {
                    'id': str(archive[0].author.id),
                    'name': archive[0].author.name,
                    'discriminator': archive[0].author.discriminator,
                    'avatar_url': '',
                    'mod': False,
                },
                'closer': {'id': str(0), 'name': 'message edited', 'discriminator': 0, 'avatar_url': ''},
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
                            'avatar_url': archive[0].author.display_avatar.with_format('png').with_size(1024).url,
                            'mod': False,
                        },
                        'attachments': [x.url for x in archive[0].attachments],
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
                            'avatar_url': archive[1].author.display_avatar.with_format('png').with_size(1024).url,
                            'mod': False,
                        },
                        'attachments': [x.url for x in archive[1].attachments],
                    },
                ],
            }
        )

    else:
        messages = []
        for msg in archive:  # TODO: attachment CDN urls should be posted as message
            messages.append(
                {
                    'timestamp': str(msg.created_at),
                    'message_id': str(msg.id),
                    'content': msg.content if msg.content else '',
                    'type': 'thread_message',
                    'author': {
                        'id': str(msg.author.id),
                        'name': msg.author.name,
                        'discriminator': msg.author.discriminator,
                        'avatar_url': msg.author.display_avatar.with_format('png').with_size(1024).url,
                        'mod': False,
                    },
                    'channel': {'id': str(msg.channel.id), 'name': msg.channel.name},
                    'attachments': [x.url for x in msg.attachments],
                }
            )

        db.insert_one(
            {
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
                    'mod': False,
                },
                'creator': {
                    'id': str(archive[0].author.id),
                    'name': archive[0].author.name,
                    'discriminator': archive[0].author.discriminator,
                    'avatar_url': '',
                    'mod': False,
                },
                'closer': {'id': str(0), 'name': 'message archived', 'discriminator': 0, 'avatar_url': ''},
                'messages': messages,
            }
        )

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
        'joins': [int(datetime.now(tz=timezone.utc).timestamp())],
        'leaves': [],
        'nameHist': [
            {
                'str': member.name,
                'type': 'name',
                'discriminator': member.discriminator,
                'timestamp': int(datetime.now(tz=timezone.utc).timestamp()),
            }
        ],
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
        'background': 'default-light',
        'backgrounds': ['default-light', 'default-dark'],
    }
    db.insert_one(userData)


async def issue_pun(
    user,
    moderator,
    _type,
    reason=None,
    expiry=None,
    active=True,
    context=None,
    _date=None,
    public=True,
    strike_count=None,
    public_notify=False,
):
    db = mclient.bowser.puns
    timestamp = time.time() if not _date else _date
    docID = str(uuid.uuid4())
    while db.find_one({'_id': docID}):  # Uh oh, duplicate uuid generated
        docID = str(uuid.uuid4())

    db.insert_one(
        {
            '_id': docID,
            'user': user,
            'moderator': moderator,
            'type': _type,
            'strike_count': strike_count,
            'active_strike_count': strike_count,
            'timestamp': int(timestamp),
            'reason': reason,
            'expiry': expiry,
            'context': context,
            'active': active,
            'sensitive': False,
            'public': public,
            'public_log_message': None,
            'public_log_channel': None,
            'public_notify': public_notify,
        }
    )
    return docID


def resolve_duration(data, include_seconds=False):
    """
    Takes a raw input string formatted 1w1d1h1m1s (any order)
    and converts to timedelta
    Credit https://github.com/b1naryth1ef/rowboat via MIT license

    data: str
    """
    value = 0
    digits = ''

    try:  # Check if any time characters exist in the data str, and raise if not
        int(data)
        raise KeyError('Time format not a valid entry')

    except:
        pass

    for char in data:
        if char.isdigit():
            digits += char
            continue

        if char not in timeUnits or not digits:
            raise KeyError('Time format not a valid entry')

        value += timeUnits[char](int(digits))
        digits = ''

    if include_seconds:
        return datetime.now(tz=timezone.utc) + timedelta(seconds=value + 1), value

    else:
        return datetime.now(tz=timezone.utc) + timedelta(seconds=value + 1)


def humanize_duration(duration):
    """
    Takes a datetime object and returns a prettified
    weeks, days, hours, minutes, seconds string output
    Credit https://github.com/ThaTiemsz/jetski via MIT license

    duration: datetime
    """
    now = datetime.now(tz=timezone.utc)
    if isinstance(duration, timedelta):
        if duration.total_seconds() > 0:
            duration = datetime.now(tz=timezone.utc) + duration
        else:
            duration = datetime.now(tz=timezone.utc) - timedelta(seconds=duration.total_seconds())
    diff_delta = duration - now
    diff = int(diff_delta.total_seconds())

    if diff < 0:
        diff = -diff
        ago = ' ago'
    else:
        ago = ''

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

    if not expires:
        return '0 seconds'
    return ', '.join(expires) + ago


def mod_cmd_invoke_delete(channel):
    return not (channel.id in config.showModCTX or channel.category_id in config.showModCTX)


async def commit_profile_change(bot, user: discord.User, element: str, item: str, revoke=False, silent=False):
    '''Given a user, update the owned status of a particular element (trophy, background, etc.), "item"'''
    # Calling functions should be verifying availability of item
    db = mclient.bowser.users
    dbUser = db.find_one({'_id': user.id})
    key = {'background': 'backgrounds', 'trophy': 'trophies'}[element]

    if item in dbUser[key] and not revoke:
        raise ValueError('Item is already granted to user')

    if item not in dbUser[key] and revoke:
        raise ValueError('Item is not granted to user')

    socialCog = bot.get_cog('Social Commands')

    if not revoke:
        db.update({'_id': user.id}, {'$push': {key: item}})
        dmMsg = f'Hey there {discord.utils.escape_markdown(user.name)}!\nYou have received a new item for your profile on the r/NintendoSwitch Discord server!\n\nThe **{item.replace("-", " ")}** {element} is now yours, enjoy! '
        if element == 'background':
            dmMsg += f'If you wish to use this background, use the `/profile background` command in our Discord server. Here\'s what your profile could look like:'
            generated_background = socialCog._generate_background_preview([item])

        else:
            dmMsg += "Here's what your profile looks like with it:"
            generated_background = await socialCog._generate_profile_card_from_member(user)

        try:
            if not silent:
                await user.send(dmMsg, file=generated_background)

        except (discord.NotFound, discord.Forbidden):
            pass

    else:
        db.update({'_id': user.id}, {'$pull': {key: item}})
        # Reset background to default if the one being revoked is currently equiped
        if dbUser['background'] == item and element == 'background':
            db.update({'_id': user.id}, {'$set': {'background': 'default-light'}})

        dmMsg = f'Hey there {discord.utils.escape_markdown(user.name)},\nA profile item has been revoked from you on the r/NintendoSwitch Discord server.\n\nThe **{item.replace("-", " ")}** {element} was revoked from you. '
        if element == 'background':
            f'If you were using this as your current background then your background has been reset to default. Use the `/profile background` command in our Discord server if you\'d like to change it.'
        dmMsg += f'If you have questions about this action, please feel free to reach out to us via modmail by DMing <@{config.parakarry}>.'

        try:
            if not silent:
                await user.send(dmMsg)

        except (discord.NotFound, discord.Forbidden):
            pass


async def send_modlog(
    bot,
    channel,
    _type,
    footer=None,
    reason=None,
    user=None,
    username=None,
    userid=None,
    moderator=None,
    expires=None,
    extra_author=None,
    timestamp=None,
    public=False,
    delay=300,
    updated=None,
    description=None,
):
    if user:
        # Keep compatibility with sources without reliable user objects (i.e. ban), without forcing a long function every time
        username = str(user)
        userid = user.id

    # Certain types require special formatting, all others are generic
    if _type in ['duration-update', 'reason-update']:
        author = config.punStrs[_type] + f' ({extra_author}) '

    elif _type == 'strike':
        author = f'{extra_author} ' + config.punStrs[_type]
        author += 's ' if extra_author > 1 else ' '

    elif _type == 'destrike':
        author = f'Removed {extra_author} ' + config.punStrs['strike']
        author += 's ' if extra_author > 1 else ' '

    else:
        author = f'{config.punStrs[_type]} '
        if extra_author:
            author += f'({extra_author}) '

    author += f'| {username} ({userid})'

    if not timestamp:
        timestamp = datetime.now(tz=timezone.utc)

    embed = discord.Embed(color=config.punColors[_type], timestamp=timestamp)
    embed.set_author(name=author)
    embed.add_field(name='User', value=f'<@!{userid}>', inline=True)

    if description:
        embed.description = description

    if footer:
        embed.set_footer(text=footer)

    if moderator:
        if not isinstance(moderator, str):  # Convert to str
            moderator = moderator.mention

        embed.add_field(name='Moderator', value=moderator, inline=True)

    if expires:
        embed.add_field(name='Expires' if _type != 'duration-update' else 'Now expires', value=expires)

    if reason:
        embed.add_field(name='Reason' if _type != 'reason-update' else 'New reason', value=reason)

    if _type == 'reason-update':
        embed.add_field(name='Old reason', value=updated)

    await channel.send(embed=embed)
    if public:
        event_loop = bot.loop
        post_action = event_loop.call_later(
            delay,
            event_loop.create_task,
            send_public_modlog(bot, footer, bot.get_channel(config.publicModChannel), expires),
        )
        return post_action


async def send_public_modlog(bot, id, channel, mock_document=None):
    db = mclient.bowser.puns
    doc = mock_document if not id else db.find_one({'_id': id})

    if not doc:
        return

    user = await bot.fetch_user(doc['user'])

    try:
        member = await channel.guild.fetch_member(doc['user'])
    except:
        member = None

    author = f'{config.punStrs[doc["type"]]} '

    if doc['type'] == 'strike':
        author = f'{doc["strike_count"]} ' + config.punStrs[doc['type']]
        author += 's ' if doc['strike_count'] > 1 else ' '

    elif doc['type'] == 'destrike':
        author = f'Removed {doc["strike_count"]} ' + config.punStrs['strike']
        author += 's ' if doc['strike_count'] > 1 else ' '

    elif doc['type'] in ['blacklist', 'unblacklist']:
        author += f'({doc["context"]}) '

    author += f'| {user} ({user.id})'

    embed = discord.Embed(
        color=config.punColors[doc['type']], timestamp=datetime.fromtimestamp(doc['timestamp'], tz=timezone.utc)
    )
    embed.set_author(name=author)
    embed.set_footer(text=id)
    embed.add_field(name='User', value=user.mention, inline=True)
    if doc['expiry']:
        embed.add_field(name='Expires', value=f'<t:{doc["expiry"]}:f> (<t:{doc["expiry"]}:R>)')
    if doc['sensitive']:
        embed.add_field(
            name='Reason',
            value='This action\'s reason has been marked sensitive by the moderation team and is hidden. See <#671003325495509012> for more information on why logs are marked sensitive',
        )
    elif doc['context'] == 'vote':  # Warning review
        embed.add_field(name='Reason', value='A moderator has reviewed a previous warning and reduced it by one level')
    else:
        embed.add_field(name='Reason', value=filter_links_from_reason(doc['reason']))

    if doc['moderator'] == bot.user.id:
        embed.description = 'This is an automatic action'

    if doc['public_notify'] and member:
        for command in bot.tree.get_commands(guild=discord.Object(id=config.nintendoswitch)):
            # Iterate over commands in the tree so we can get the profile command ID
            if command.name == 'profile':
                break

        commandID = command.extras['id']
        content = f'{user.mention}, I was unable to DM you for this infraction. Use the </history:{commandID}> command in any channel for further details.'
    else:
        content = None

    message = await channel.send(content, embed=embed)

    if id:
        db.update_one({'_id': id}, {'$set': {'public_log_message': message.id, 'public_log_channel': channel.id}})


def filter_links_from_reason(reason):
    links = reasonFilterLinkRe.finditer(reason)
    if links:
        for l in links:
            original_link = l[0]
            link = l[0]

            # Add a scheme per RFC 1808 if one does not exist
            if not link.lower().startswith('http'):
                link = 'no-proto-sentinel://' + link

            try:
                urlParts = urllib.parse.urlsplit(link)
            except ValueError:  # Invalid URL edge case
                print('ValueError')
                continue

            urlPartsList = list(urlParts)

            # We can now modify the link
            # For each domain level of hostname, eg. foo.bar.example => foo.bar.example, bar.example, example
            labels = urlPartsList[1].split(".")
            whitelisted = False
            for i in range(0, len(labels)):
                domain = ".".join(labels[i - len(labels) :])
                if domain.lower() in reasonFilterWhitelistedDomains:
                    whitelisted = True
                    break

            # Not in whitelist
            if not whitelisted:
                urlPartsList[1] = ''.join(['•' if c != '.' else '.' for c in urlPartsList[1]])

            urlPartsList[2] = ''.join(['•' if c != '/' else '/' for c in urlPartsList[2]])

            # We now pack up this link
            url = urllib.parse.urlunsplit(urlPartsList)

            if link.lower().startswith('no-proto-sentinel://'):
                url = url[20:]

            if original_link != url:
                reason = reason.replace(original_link, f'`{url}`')

    return reason


def format_pundm(_type, reason, moderator=None, details=None, auto=False):
    details_int = details if isinstance(details, int) else 0
    details_str = details if isinstance(details, str) else ""
    details_tup = details if isinstance(details, tuple) else ("", "")
    infoStrs = {
        'strike': f'You have received **{details_int} strike{"s" if details_int > 1 else ""}** on',
        'destrike': f'Your **active strikes** have been reduced by **{details_int} strike{"s" if details_int > 1 else ""}** on',
        'warn': f'You have been **warned (now {details_str})** on',
        'warnup': f'Your **warning level** has been **increased (now {details_str})** on',
        'warndown': f'Your **warning level** has been **decreased (now {details_str})** on',
        'warnclear': f'Your **warning** has been **cleared** on',
        'mute': f'You have been **muted ({details_str})** on',
        'unmute': f'Your **mute** has been **removed** on',
        'blacklist': f'Your **{details_str} permissions** have been **restricted** on',
        'unblacklist': f'Your **{details_str} permissions** have been **restored** on',
        'kick': 'You have been **kicked** from',
        'ban': 'You have been **banned** from',
        'automod-word': 'You have violated the word filter on',
        'duration-update': f'The **duration** for your {details_tup[0]} has been updated and **will now expire on {details_tup[1]}** on',
        'reason-update': f'The **reasoning** for your {details_tup[0]} issued on {details_tup[1]} has been updated on',
    }

    punDM = infoStrs[_type] + f' the /r/NintendoSwitch Discord server.\n'
    if _type == 'reason-update':
        punDM += f'Updated reason: ```{reason}``` '
    else:
        punDM += f'Reason: ```{reason}``` '

    if moderator:
        mod = f'{moderator} ({moderator.mention})' if not auto else 'Automatic action'
        punDM += f'Responsible moderator: {mod}\n\n'

    if details == 'modmail':
        punDM += 'If you have questions concerning this matter, please feel free to contact the moderator that took this action or another member of the moderation team.\n'

    elif _type == 'ban':
        punDM += f'If you would like to appeal this ban, you may join our ban appeal server to dispute it with the moderation team: {config.banAppealInvite}\n'

    elif _type in ('strike', 'destrike'):
        punDM += f'You can have a maximum of 16 active strikes, any infraction that would put you over this amount will lead to a ban. Strikes are not permanent and will decay at a rate of 1 per week. If you are struck again before all have expired, your count will be increased and the 1 week decay timer is reset.\n\n'

    if _type != 'ban' and details != 'modmail':
        punDM += f'If you have questions concerning this matter you may contact the moderation team by sending a DM to our modmail bot, Parakarry (<@{config.parakarry}>).\n'

    punDM += 'Please do not respond to this message, I cannot reply.'

    return punDM


def spans_overlap_link(string: str, spans: typing.List[typing.Tuple[int, int]]) -> typing.List[bool]:
    """
    Returns list of booleans for every character span passed (as `(start, end)`) if they overlap a link in given string.
    """
    START, END = (0, 1)  # Consts for readablity of (start, end) tuples

    links = linkRe.finditer(string)

    if not spans:
        return []
    if not links:
        return [False] * len(spans)

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
    """
    Returns if any regex match for given pattern in string does not overlap a link.

    Returns:
    True  - At least one non link-overlapping match was found.
    False - All matches overlapped a link.
    None  - No match found, regardless of link overlap.
    """
    matches = list(re.finditer(pattern, string))
    spans = list(map(lambda m: m.span(), matches))

    if not matches:
        return None

    overlaps = spans_overlap_link(string, spans)
    return any(not overlap for overlap in overlaps)


class PaginatedEmbed(discord.ui.View):
    '''
    Displays an interactive paginated embed of given fields, with optional owner-locking, until timed out.
    Interactions should be deferred with `thinking=True` before instantiating.
    '''

    PAGE_TEMPLATE = 'Page {0}/{1}'
    FOOTER_ENDED_BY = 'Ended by {0}'
    MESSAGE: discord.WebhookMessage | None = None

    def __init__(
        self,
        interaction: discord.Interaction,
        fields: typing.List[typing.Dict],  # name: str , value: str, inline: optional bool
        *,
        title: typing.Optional[str] = '',
        description: typing.Optional[str] = None,
        color: typing.Union[discord.Colour, int, None] = None,
        author: typing.Optional[typing.Dict] = None,
        page_character_limit: typing.Optional[int] = 6000,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)

        self.initial_interaction = interaction
        self.bot = interaction.client
        self.owner = interaction.user
        self.title = title

        # Find the page character cap
        footer_max_length = (
            len(self.PAGE_TEMPLATE) + len(self.FOOTER_ENDED_BY.format('-' * 37)) + 4
        )  # 37 = max len(discordtag...#0000)
        title_max_length = len(title) + 1
        description_length = 0 if not description else len(description)
        author_length = 0 if not author else len(author['name'])

        self.page_char_cap = (
            page_character_limit - footer_max_length - title_max_length - description_length - author_length
        )

        self.embed = discord.Embed(description=None if not description else description, color=color)
        if author:
            self.embed.set_author(
                name=author['name'], icon_url=None if not 'icon_url' in author else author['icon_url']
            )
        self.embed.set_footer(icon_url=None if not self.owner else self.owner.display_avatar.url)

        self.build_pages(interaction, fields)
        embed = self.generate_new_embed()
        if self.single_page:
            interaction.client.loop.call_soon(
                interaction.client.loop.create_task,
                self.process_response(embed, interaction=interaction, button_interact=False),
            )

            self.stop()  # If one page is required, exit the view

        else:
            self.ui_setup()  # Create our UI elements
            interaction.client.loop.call_soon(
                interaction.client.loop.create_task,
                self.process_response(embed, interaction=interaction, button_interact=False),
            )

    def ui_setup(self):
        # Create components and assign them callbacks
        self.add_item(discord.ui.Button(label='⬅️ Previous', disabled=True, style=discord.ButtonStyle.success))
        self.add_item(discord.ui.Button(label='Next ➡️', style=discord.ButtonStyle.success))
        self.add_item(discord.ui.Button(label='⏹️ End', style=discord.ButtonStyle.secondary))

        callbacks = [self.regress_page, self.progress_page, self.end_pagination]

        for index, child in enumerate(self.children):
            child.callback = callbacks[index]

    async def regress_page(self, interaction: discord.Interaction):
        if self.current_page - 1 <= 1:
            self.children[0].disabled = True

        if self.children[1].disabled:
            self.children[1].disabled = False

        self.current_page -= 1
        await self.process_response(self.generate_new_embed(), interaction=interaction, button_interact=True)

    async def end_pagination(self, interaction: discord.Interaction | None):
        page_text = self.PAGE_TEMPLATE.format(self.current_page, len(self.pages))
        footer_text = 'Timed out' if not interaction else self.FOOTER_ENDED_BY.format(str(interaction.user))
        self.embed.set_footer(text=f'{page_text}    {footer_text}', icon_url=self.embed.footer.icon_url)

        button_interact = True if interaction else False
        interaction = self.initial_interaction if not interaction else interaction

        await self.process_response(
            self.embed, interaction=interaction, button_interact=button_interact, remove_view=True
        )
        self.stop()

    async def progress_page(self, interaction: discord.Interaction):
        if self.current_page + 1 >= len(self.pages):
            self.children[1].disabled = True

        if self.children[0].disabled:
            self.children[0].disabled = False

        self.current_page += 1
        await self.process_response(self.generate_new_embed(), interaction=interaction, button_interact=True)

    async def on_timeout(self):
        await self.end_pagination(None)

    async def process_response(
        self,
        embed: discord.Embed,
        *,
        interaction: discord.Interaction,
        button_interact: bool,
        remove_view: bool = False,
    ):
        if button_interact:
            # Button interactions require us to perform another interaction.response
            if remove_view:
                await interaction.response.edit_message(embed=embed, view=None)

            else:
                await interaction.response.edit_message(embed=embed, view=self)

        else:
            if not self.MESSAGE:
                self.MESSAGE = await interaction.original_response()

            if remove_view:
                await self.MESSAGE.edit(embed=embed, view=None)

            else:
                await self.MESSAGE.edit(embed=embed)

    def build_pages(self, interaction: discord.Interaction, fields: typing.List[typing.Dict]):
        self.pages = []
        while fields:
            remaining_chars = self.page_char_cap
            page = []

            # Make sure a field won't max out this page
            for field in fields.copy():
                field_length = len(field['name']) + len(field['value'])

                if remaining_chars - field_length < 0:
                    break
                remaining_chars -= field_length

                page.append(fields.pop(0))

                if len(page) == 25:
                    break

            self.pages.append(page)

        self.current_page = 1
        self.ended_by = None

        self.single_page = len(self.pages) <= 1

    def generate_new_embed(self) -> discord.Embed:
        self.embed.clear_fields()
        if len(self.pages) != 0:
            for field in self.pages[self.current_page - 1]:
                self.embed.add_field(
                    name=field['name'], value=field['value'], inline=True if not 'inline' in field else field['inline']
                )

        page_text = self.PAGE_TEMPLATE.format(self.current_page, max(len(self.pages), 1))
        self.embed.title = f'{self.title} {page_text}'

        if self.single_page:
            self.embed.set_footer(text=page_text)

        else:
            self.embed.set_footer(text=page_text, icon_url=self.embed.footer.icon_url)

        return self.embed


def convert_list_to_fields(lines: str, codeblock: bool = True) -> typing.List[typing.Dict]:
    fields = []

    while lines:
        value = '```' if codeblock else ''

        for line in lines.copy():
            staged = value + line + '\n'
            if len(staged) + (3 if codeblock else 0) > 1024:
                break

            lines.pop(0)
            value = staged

        value += '```' if codeblock else ''
        fields.append({'name': '\ufeff', 'value': value, 'inline': False})  # \uFEFF = ZERO WIDTH NO-BREAK SPACE

    return fields


class ResolveUser(discord.ext.commands.Converter):
    async def convert(self, ctx, argument):
        if not argument:
            raise discord.ext.commands.BadArgument

        try:
            userid = int(argument)

        except ValueError:
            mention = re.search(r'<@!?(\d+)>', argument)
            if not mention:
                raise discord.ext.commands.BadArgument

            userid = int(mention.group(1))

        try:
            member = ctx.guild.get_member(userid)
            user = await ctx.bot.fetch_user(argument) if not member else member
            return user

        except discord.NotFound:
            raise discord.ext.commands.BadArgument


async def setup(bot):
    logging.info('[Extension] Utils module loaded')


async def teardown(bot):
    logging.info('[Extension] Utils module unloaded')
