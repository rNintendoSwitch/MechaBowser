import asyncio
import logging
import re
import typing
import datetime
import time
import aiohttp
import urllib
import pathlib

import pymongo
import discord
from discord import Webhook, AsyncWebhookAdapter
from discord.ext import commands, tasks

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

serverLogs = None
modLogs = None

# Most NintenDeals code (decommissioned 4/25/2020) was removed on 12/02/2020
# https://github.com/rNintendoSwitch/MechaBowser/commit/3da2973f3b48548403c24d38c33cdd3a196ac409
class Games(commands.Cog, name='Game Commands'):
    gamesReady = False
    def __init__(self, bot):
        self.bot = bot
        self.games = {}
        self.gamesReady = False
        self.session = aiohttp.ClientSession()

        self.update_game_info.start() #pylint: disable=no-member
        logging.info('[Deals] Games task cogs loaded')

    def cog_unload(self):
        logging.info('[Deals] Attempting to cancel tasks...')
        self.update_game_info.cancel() #pylint: disable=no-member
        logging.info('[Deals] Tasks exited')
        asyncio.get_event_loop().run_until_complete(self.session.close())
        logging.info('[Deals] Games task cogs unloaded')

    async def _ready_status(self):
        return self.gamesReady

    # We still use game info for profiles
    @tasks.loop(seconds=43200)
    async def update_game_info(self):
        logging.info('[Deals] Starting game fetch')
        gameDB = mclient.bowser.games

        games = gameDB.find({})
        for game in games:
            scores = {'metascore': game['scores']['metascore'], 'userscore': game['scores']['userscore']}
            gameEntry = {
                    '_id': game['_id'],
                    'nsuids': game['nsuids'],
                    'titles': game['titles'],
                    'release_dates': game['release_dates'],
                    'categories': game['categories'],
                    'websites': game['websites'],
                    'scores': scores,
                    'free_to_play': game['free_to_play']
                }
            self.games[game['_id']] = gameEntry

        self.gamesReady = True
        logging.info('[Deals] Finished game fetch')

    @commands.cooldown(1, 15, type=commands.cooldowns.BucketType.member)
    @commands.command(name='games', aliases=['game'])
    async def _games(self, ctx):
        return await ctx.send(f'{ctx.author.mention} {config.redTick} Game searching and fetching has been temporarily disabled. For more information see https://www.reddit.com/r/NintendoSwitch/comments/g7w97x/')

class ChatControl(commands.Cog, name='Utility Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.boostChannel = self.bot.get_channel(config.boostChannel)
        self.voiceTextChannel = self.bot.get_channel(config.voiceTextChannel)
        self.voiceTextAccess = self.bot.get_guild(config.nintendoswitch).get_role(config.voiceTextAccess)
        self.SMM2LevelID = re.compile(r'([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})', re.I | re.M)
        self.SMM2LevelPost = re.compile(r'Name: ?(\S.*)\n\n?(?:Level )?ID:\s*((?:[0-9a-z]{3}-){2}[0-9a-z]{3})(?:\s+)?\n\n?Style: ?(\S.*)\n\n?(?:Theme: ?(\S.*)\n\n?)?(?:Tags: ?(\S.*)\n\n?)?Difficulty: ?(\S.*)\n\n?Description: ?(\S.*)', re.I)
        self.affiliateTags = {
            "*": ["awc"],
            "amazon.*": ["colid", "coliid", "tag"],
            "bestbuy.*": ["aid", "cjpid", "lid", "pid"], 
            "bhphotovideo.com": ["sid"],
            "ebay.*": ["afepn", "campid", "pid"],
            "gamestop.com": ["affid", "cid", "sourceid"],
            "groupon.*": ["affid"],
            "newegg*.*": ["aid", "pid"],
            "play-asia.com": ["tagid"],
            "stacksocial.com": ["aid", "rid"],
            "store.nintendo.co.uk": ["affil"],
            "tigerdirect.com": ["affiliateid", "srccode"],
            "walmart.*": ["sourceid", "veh", "wmlspartner"],
            }
        self.inviteRe = re.compile(r'((?:https?:\/\/)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com\/invite)\/[\da-z-]+)', re.I)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel: # If other info than channel (such as mute status), ignore
            return

        if not before.channel: # User just joined a channel
            await member.add_roles(self.voiceTextAccess)

        elif not after.channel: # User just left a channel or moved to AFK
            try:
                await member.remove_roles(self.voiceTextAccess)

            except:
                mclient.bowser.users.update_one({'_id': member.id}, {'$pull': {'roles': config.voiceTextAccess}})
            
    # Called after automod filter finished, because of the affilite link reposter. We also want to wait for other items in this function to complete to call said reposter.
    async def on_automod_finished(self, message):
        if message.type == discord.MessageType.premium_guild_subscription:
            await self.adminChannel.send(message.system_content)
            await self.boostChannel.send(message.system_content)

        if message.author.bot or message.type != discord.MessageType.default:
            return

        #Filter invite links
        msgInvites = re.findall(self.inviteRe, message.content)
        if msgInvites and config.moderator not in [x.id for x in message.author.roles]:
            guildWhitelist = mclient.bowser.guilds.find_one({'_id': message.guild.id})['inviteWhitelist']
            fetchedInvites = []
            inviteInfos = []
            for x in msgInvites:
                try:
                    if x not in fetchedInvites:
                        fetchedInvites.append(x)
                        invite = await self.bot.fetch_invite(x)
                        if invite.guild.id in guildWhitelist: continue
                        if 'VERIFIED' in invite.guild.features: continue
                        if 'PARTNERED' in invite.guild.features: continue

                        inviteInfos.append(invite)

                except (discord.NotFound, discord.HTTPException):
                    inviteInfos.append(x)

            if inviteInfos:
                await message.delete()
                await message.channel.send(f':bangbang: {message.author.mention} please do not post invite links to other Discord servers. If you believe the linked server(s) should be whitelisted, contact a moderator', delete_after=10)
                await self.adminChannel.send(f'⚠️ {message.author.mention} has posted a message with one or more invite links in {message.channel.mention} and has been deleted.\nInvite(s): {" | ".join(msgInvites)}')

        #Filter for #mario
        if message.channel.id == config.marioluigiChannel: # #mario
            if utils.re_match_nonlink(self.SMM2LevelID, message.content):
                await message.delete()
                response = await message.channel.send(f'{config.redTick} <@{message.author.id}> Please do not post Super Mario Maker 2 level codes ' \
                    f'here. Post in <#{config.smm2Channel}> with the pinned template instead.')

                await response.delete(delay=20)
            return

        #Filter for #smm2-levels
        if message.channel.id == config.smm2Channel:
            if not re.search(self.SMM2LevelID, message.content):
                # We only want to filter posts with a level id
                return

            block = re.search(self.SMM2LevelPost, message.content)
            if not block:
                # No match for a properly formatted level post
                response = await message.channel.send(f'{config.redTick} <@{message.author.id}> Your level is formatted incorrectly, please see the pinned messages for the format. A copy '\
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

            try:
                await message.channel.send(embed=embed)
                await message.delete()

            except discord.errors.Forbidden:
                # Fall back to leaving user text
                logging.error(f'[Filter] Unable to send embed to {message.channel.id}')
            return

        # Filter and clean affiliate links
        # We want to call this last to ensure all above items are complete.
        links = utils.linkRe.finditer(message.content)
        if links: 
            contentModified = False
            content = message.content
            for link in links:
                linkModified = False

                urlParts = urllib.parse.urlsplit(link[0])
                urlPartsList = list(urlParts)

                query_raw = dict(urllib.parse.parse_qsl(urlPartsList[3]))
                # Make all keynames lowercase in dict, this shouldn't break a website, I hope...
                query = {k.lower(): v for k, v in query_raw.items()}

                # For each domain level of hostname, eg. foo.bar.example => foo.bar.example, bar.example, example
                labels = urlParts.hostname.split(".")
                for i in range(0, len(labels)):
                    domain = ".".join(labels[i - len(labels):])
                    
                    for glob, tags in self.affiliateTags.items():
                        if pathlib.PurePath(domain).match(glob):
                            for tag in tags:
                                if tag in query:
                                    linkModified = True
                                    query.pop(tag, None)
                
                if linkModified:
                    urlPartsList[3] = urllib.parse.urlencode(query)
                    url = urllib.parse.urlunsplit(urlPartsList)

                    contentModified = True
                    content = content.replace(link[0], url)

            if contentModified:
                hooks = await message.channel.webhooks()
                useHook = await message.channel.create_webhook(name=f'mab_{message.channel.id}', reason='No webhooks existed; 1<= required for chat filtering') if not hooks else hooks[0]
            
                await message.delete()
                async with aiohttp.ClientSession() as session:
                    name = message.author.name if not message.author.nick else message.author.nick
                    webhook = Webhook.from_url(useHook.url, adapter=AsyncWebhookAdapter(session))
                    await webhook.send(content=content, username=name, avatar_url=message.author.avatar_url)

# Large block of old event commented out code was removed on 12/02/2020
# Includes: Holiday season celebration, 30k members celebration, Splatoon splatfest event, Pokemon sword/shield event
# https://github.com/rNintendoSwitch/MechaBowser/commit/373cef69aa5b9da7fe5945599b7dde387caf0700

#    @commands.command(name='archive')
#    async def _archive(self, ctx, members: commands.Greey[discord.Member], channels: commands.Greedy[discord.Channel], limit: typing.Optional[int] = 200, channel_limiter: typing.Greedy[discord.Channel]):
#        pass

    @commands.command(name='clean')
    @commands.has_any_role(config.moderator, config.eh)
    async def _clean(self, ctx, messages: int, members: commands.Greedy[discord.Member]):
        if messages >= 100:
            def confirm_check(reaction, member):
                return member == ctx.author and str(reaction.emoji) in [config.redTick, config.greenTick]

            confirmMsg = await ctx.send(f'This action will delete up to {messages}, are you sure you want to proceed?')
            await confirmMsg.add_reaction(config.greenTick)
            await confirmMsg.add_reaction(config.redTick)
            try:
                reaction = await self.bot.wait_for('reaction_add', timeout=15, check=confirm_check)
                if str(reaction[0]) != config.greenTick:
                    await confirmMsg.edit(content='Clean action canceled.')
                    return await confirmMsg.clear_reactions()

            except asyncio.TimeoutError:
                await confirmMsg.edit(content='Confirmation timed out, clean action canceled.')
                return await confirmMsg.clear_reactions()

            else:
                await confirmMsg.delete()
            
        memberList = None if not members else [x.id for x in members]

        def message_filter(message):
            return True if not memberList or message.author.id in memberList else False

        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=messages, check=message_filter, bulk=True)
    
        m = await ctx.send(f'{config.greenTick} Clean action complete')
        #archiveID = await utils.message_archive(list(reversed(deleted)))

        #embed = discord.Embed(description=f'Archive URL: {config.baseUrl}/archive/{archiveID}', color=0xF5A623, timestamp=datetime.datetime.utcnow())
        #await self.bot.get_channel(config.logChannel).send(f':printer: New message archive generated for {ctx.channel.mention}', embed=embed)

        return await m.delete(delay=5)

    @commands.command(name='info')
    @commands.has_any_role(config.moderator, config.eh)
    async def _info(self, ctx, user: typing.Union[discord.Member, int]):
        inServer = True
        if type(user) == int:
            # User doesn't share the ctx server, fetch it instead
            dbUser = mclient.bowser.users.find_one({'_id': user})
            inServer = False
            try:
                user = await self.bot.fetch_user(user)

            except discord.NotFound:
                return await ctx.send(f'{config.redTick} User does not exist')

            if not dbUser:
                embed = discord.Embed(color=discord.Color(0x18EE1C), description=f'Fetched information about {user.mention} from the API because they are not in this server. There is little information to display as they have not been recorded joining the server before.')
                embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
                embed.set_thumbnail(url=user.avatar_url)
                embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'))
                return await ctx.send(embed=embed) # TODO: Return DB info if it exists as well

        else:
            dbUser = mclient.bowser.users.find_one({'_id': user.id})

        # Member object, loads of info to work with
        messages = mclient.bowser.messages.find({'author': user.id})
        msgCount = 0 if not messages else messages.count()

        desc = f'Fetched user {user.mention}' if inServer else f'Fetched information about previous member {user.mention} ' \
            'from the API because they are not in this server. ' \
            'Showing last known data from before they left.'



        embed = discord.Embed(color=discord.Color(0x18EE1C), description=desc)
        embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
        embed.set_thumbnail(url=user.avatar_url)
        embed.add_field(name='Messages', value=str(msgCount), inline=True)
        if inServer:
            embed.add_field(name='Join date', value=user.joined_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)
        roleList = []
        if inServer:
            for role in reversed(user.roles):
                if role.id == user.guild.id:
                    continue

                roleList.append(role.name)

        else:
            roleList = dbUser['roles']
            
        if not roleList:
            # Empty; no roles
            roles = '*User has no roles*'

        else:
            if not inServer:
                tempList = []
                for x in reversed(roleList):
                    y = ctx.guild.get_role(x)
                    name = '*deleted role*' if not y else y.name
                    tempList.append(name)

                roleList = tempList

            roles = ', '.join(roleList)

        embed.add_field(name='Roles', value=roles, inline=False)

        lastMsg = 'N/a' if msgCount == 0 else datetime.datetime.utcfromtimestamp(messages.sort('timestamp',pymongo.DESCENDING)[0]['timestamp']).strftime('%B %d, %Y %H:%M:%S UTC')
        embed.add_field(name='Last message', value=lastMsg, inline=True)
        embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)

        noteDocs = mclient.bowser.puns.find({'user': user.id, 'type': 'note'})
        fieldValue = 'View history to get full details on all notes.\n\n'
        if noteDocs.count():
            noteCnt = noteDocs.count()
            noteList = []
            for x in noteDocs.sort('timestamp', pymongo.DESCENDING):
                stamp = datetime.datetime.utcfromtimestamp(x['timestamp']).strftime('`[%m/%d/%y]`')
                noteContent = f'{stamp}: {x["reason"]}'

                fieldLength = 0
                for value in noteList: fieldLength += len(value)
                if len(noteContent) + fieldLength > 924:
                    fieldValue = f'Only showing {len(noteList)}/{noteCnt} notes. ' + fieldValue
                    break

                noteList.append(noteContent)

            embed.add_field(name='User notes', value=fieldValue + '\n'.join(noteList), inline=False)

        punishments = ''
        punsCol = mclient.bowser.puns.find({'user': user.id, 'type': {'$ne': 'note'}})
        if not punsCol.count():
            punishments = '__*No punishments on record*__'

        else:
            puns = 0
            for pun in punsCol.sort('timestamp', pymongo.DESCENDING):
                if puns >= 5:
                    break

                puns += 1
                stamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%m/%d/%y %H:%M:%S UTC')
                punType = config.punStrs[pun['type']]
                if pun['type'] in ['clear', 'unmute', 'unban', 'unblacklist']:
                    punishments += f'- [{stamp}] {punType}\n'

                else:
                    punishments += f'+ [{stamp}] {punType}\n'

            punishments = f'Showing {puns}/{punsCol.count()} punishment entries. ' \
                f'For a full history including responsible moderator, active status, and more use `{ctx.prefix}history @{str(user)}` or `{ctx.prefix}history {user.id}`' \
                f'\n```diff\n{punishments}```'
        embed.add_field(name='Punishments', value=punishments, inline=False)
        return await ctx.send(embed=embed)

    @commands.command(name='history')
    @commands.has_any_role(config.moderator, config.eh)
    async def _history(self, ctx, user: typing.Union[discord.User, int]):
        if type(user) == int:
            # User doesn't share the ctx server, fetch it instead
            try:
                user = await self.bot.fetch_user(user)

            except discord.NotFound:
                return await ctx.send(f'{config.redTick} User does not exist')

        db = mclient.bowser.puns
        puns = db.find({'user': user.id})
        if not puns.count():
            return await ctx.send(f'{config.redTick} User has no punishments on record')

        punNames = {
            'tier1': 'T1 Warn',
            'tier2': 'T2 Warn',
            'tier3': 'T3 Warn',
            'clear': 'Warn Clear',
            'mute': 'Mute',
            'unmute': 'Unmute',
            'kick': 'Kick',
            'ban': 'Ban',
            'unban': 'Unban',
            'blacklist': 'Blacklist ({})',
            'unblacklist': 'Unblacklist ({})',
            'appealdeny': 'Denied ban appeal (until {})',
            'note': 'User note'
        }

        if puns.count() == 1:
            desc = f'There is __1__ infraction record for this user:'

        else:
            desc = f'There are __{puns.count()}__ infraction records for this user:'

        embed = discord.Embed(title='Infraction History', description=desc, color=0x18EE1C)
        embed.set_author(name=f'{user} | {user.id}', icon_url=user.avatar_url)

        for pun in puns.sort('timestamp', pymongo.DESCENDING):
            datestamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%b %d, %y %H:%M UTC')
            moderator = ctx.guild.get_member(pun['moderator'])
            if not moderator:
                moderator = await self.bot.fetch_user(pun['moderator'])

            if pun['type'] in ['blacklist', 'unblacklist']:
                inf = punNames[pun['type']].format(pun['context'])

            elif pun['type'] == 'appealdeny':
                inf = punNames[pun['type']].format(datetime.datetime.utcfromtimestamp(pun['expiry']).strftime('%b. %d, %Y'))

            else:
                inf = punNames[pun['type']]

            embed.add_field(name=datestamp, value=f'**Moderator:** {moderator}\n**Details:** [{inf}] {pun["reason"]}')

        return await ctx.send(embed=embed)

    @commands.command(name='roles')
    @commands.has_any_role(config.moderator, config.eh)
    async def _roles(self, ctx):
        roleList = 'List of roles in guild:\n```\n'
        for role in reversed(ctx.guild.roles):
            roleList += f'{role.name} ({role.id})\n'

        await ctx.send(f'{roleList}```')

    @commands.group(name='tag', aliases=['tags'], invoke_without_command=True)
    async def _tag(self, ctx, *, query=None):
        db = mclient.bowser.tags
        await ctx.message.delete()

        if query:
            query = query.lower()
            tag = db.find_one({'_id': query, 'active': True})

            if not tag:
                return await ctx.send(f'{config.redTick} A tag with that name does not exist', delete_after=10)

            embed = discord.Embed(title=tag['_id'], description=tag['content'])
            return await ctx.send(embed=embed)

        else:
            tagList = []
            for x in db.find({'active': True}):
                tagList.append(x['_id'])

            embed = discord.Embed(title='Tag List', description='Here is a list of tags you can access:\n\n' + ', '.join(tagList))
            return await ctx.send(embed=embed)

    @_tag.command(name='edit')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_create(self, ctx, name, *, content):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})
        await ctx.message.delete()
        if name in ['edit', 'delete', 'source']:
            return await ctx.send(f'{config.redTick} You cannot use that name for a tag', delete_after=10)

        if tag:
            db.update_one({'_id': tag['_id']},
                {'$push': {'revisions': {str(int(time.time())): {'content': tag['content'], 'user': ctx.author.id}}},
                '$set': {'content': content, 'active': True}
            })
            msg = f'{config.greenTick} The **{name}** tag has been '
            msg += 'updated' if tag['active'] else 'created'
            return await ctx.send(msg, delete_after=10)

        else:
            db.insert_one({'_id': name, 'content': content, 'revisions': [], 'active': True})
            return await ctx.send(f'{config.greenTick} The **{name}** tag has been created', delete_after=10)

    @_tag.command(name='delete')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_delete(self, ctx, *, name):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})
        await ctx.message.delete()
        if tag:
            def confirm_check(reaction, member):
                return member == ctx.author and str(reaction.emoji) in [config.redTick, config.greenTick]

            confirmMsg = await ctx.send(f'This action will delete the tag "{name}", are you sure you want to proceed?')
            await confirmMsg.add_reaction(config.greenTick)
            await confirmMsg.add_reaction(config.redTick)
            try:
                reaction = await self.bot.wait_for('reaction_add', timeout=15, check=confirm_check)
                if str(reaction[0]) != config.greenTick:
                    await confirmMsg.edit(content='Delete canceled')
                    return await confirmMsg.clear_reactions()

            except asyncio.TimeoutError:
                await confirmMsg.edit(content='Reaction timed out. Rerun command to try again')
                return await confirmMsg.clear_reactions()

            else:
                db.update_one({'_id': name}, {'$set': {'active': False}})
                await confirmMsg.edit(content=f'{config.greenTick} The "{name}" tag has been deleted')
                await confirmMsg.clear_reactions()

        else:
            return await ctx.send(f'{config.redTick} The tag "{name}" does not exist')

    @_tag.command(name='source')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_source(self, ctx, *, name):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})
        await ctx.message.delete()

        if tag:
            embed = discord.Embed(title=f'{name} source', description=f'```\n{tag["content"]}\n```')
            return await ctx.send(embed=embed)

        else:
            return await ctx.send(f'{config.redTick} The tag "{name}" does not exist')

    @commands.command(name='blacklist')
    @commands.has_any_role(config.moderator, config.eh)
    async def _roles_set(self, ctx, member: discord.Member, channel: typing.Union[discord.TextChannel, discord.CategoryChannel, str], *, reason='-No reason specified-'):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Blacklist reason is too long, reduce it by at least {len(reason) - 990} characters')
        statusText = ''
        if type(channel) == str:
            # Arg blacklist
            if channel in ['mail', 'modmail']:
                context = 'modmail'
                mention = context
                users = mclient.bowser.users
                dbUser = users.find_one({'_id': member.id})

                if dbUser['modmail']:
                    users.update_one({'_id': member.id}, {'$set': {'modmail': False}})
                    statusText = 'Blacklisted'

                else:
                    users.update_one({'_id': member.id}, {'$set': {'modmail': True}})
                    statusText = 'Unblacklisted'

            else:
                return await ctx.send(f'{config.redTick} You cannot blacklist a user from that function')

        elif channel.id == config.suggestions:
            context = channel.name
            mention = channel.mention
            suggestionsRole = ctx.guild.get_role(config.noSuggestions)
            if suggestionsRole in member.roles: # Toggle role off
                await member.remove_roles(suggestionsRole)
                statusText = 'Unblacklisted'

            else: # Toggle role on
                await member.add_roles(suggestionsRole)
                statusText = 'Blacklisted'

        elif channel.id == config.spoilers:
            context = channel.name
            mention = channel.mention
            spoilersRole = ctx.guild.get_role(config.noSpoilers)
            if spoilersRole in member.roles: # Toggle role off
                await member.remove_roles(spoilersRole)
                statusText = 'Unblacklisted'

            else: # Toggle role on
                await member.add_roles(spoilersRole)
                statusText = 'Blacklisted'         

        elif channel.category_id == config.eventCat:
            context = 'events'
            mention = context
            eventsRole = ctx.guild.get_role(config.noEvents)
            if eventsRole in member.roles: # Toggle role off
                await member.remove_roles(eventsRole)
                statusText = 'Unblacklisted'

            else: # Toggle role on
                await member.add_roles(eventsRole)
                statusText = 'Blacklisted'   

        else:
            return await ctx.send(f'{config.redTick} You cannot blacklist a user from that channel')

        db = mclient.bowser.puns
        if statusText.lower() == 'blacklisted':
            docID = await utils.issue_pun(member.id, ctx.author.id, 'blacklist', reason, context=context)

        else:
            db.find_one_and_update({'user': member.id, 'type': 'blacklist', 'active': True, 'context': context}, {'$set':{
            'active': False
            }})
            docID = await utils.issue_pun(member.id, ctx.author.id, 'unblacklist', reason, active=False, context=context)

        embed = discord.Embed(color=discord.Color(0xF5A623), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{statusText} | {str(member)}')
        embed.set_footer(text=docID)
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=ctx.author.mention, inline=True)
        embed.add_field(name='Channel', value=mention)
        embed.add_field(name='Reason', value=reason)

        await self.modLogs.send(embed=embed)
        await utils.send_modlog(self.bot, self.modLogs, statusText.lower()[:-2], docID, reason, user=member, moderator=ctx.author, extra_author=context, public=True)

        try:
            statusText = 'blacklist' if statusText == 'Blacklisted' else 'unblacklist'
            await member.send(utils.format_pundm(statusText, reason, ctx.author, mention))

        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} has been {statusText.lower()}ed from {mention}')

    @_clean.error
    @_info.error
    @_history.error
    @_roles.error
    @_roles_set.error
    @_tag.error
    @_tag_create.error
    @_tag_delete.error
    @_tag_source.error
    async def utility_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(f'{config.redTick} You are using that command too fast, try again in a few seconds', delete_after=15)

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command.', delete_after=15)

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.', delete_after=15)
            raise error

class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.muteRole = self.bot.get_guild(config.nintendoswitch).get_role(config.mute)
        self.messages = {}

    @commands.Cog.listener()
    async def on_message(self, message):
        self.messages[message.channel.id].append({'user': message.author.id, 'content': message.content, 'id': message.id})

        # Individual user spam analysis
        

def setup(bot):
    global serverLogs
    global modLogs

    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)

    bot.add_cog(ChatControl(bot))
    bot.add_cog(Games(bot))
    logging.info('[Extension] Utility module loaded')

def teardown(bot):
    bot.remove_cog('ChatControl')
    bot.remove_cog('Games')
    logging.info('[Extension] Utility module unloaded')
