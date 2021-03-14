import asyncio
import datetime
import logging
import pathlib
import re
import time
import typing
import urllib

import aiohttp
import config
import discord
import pymongo
from discord import AsyncWebhookAdapter, Webhook
from discord.ext import commands, tasks

import tools


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)

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

        self.update_game_info.start()  # pylint: disable=no-member
        logging.info('[Deals] Games task cogs loaded')

    def cog_unload(self):
        logging.info('[Deals] Attempting to cancel tasks...')
        self.update_game_info.cancel()  # pylint: disable=no-member
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
                'free_to_play': game['free_to_play'],
            }
            self.games[game['_id']] = gameEntry

        self.gamesReady = True
        logging.info('[Deals] Finished game fetch')

    @commands.cooldown(1, 15, type=commands.cooldowns.BucketType.member)
    @commands.command(name='games', aliases=['game'])
    async def _games(self, ctx):
        return await ctx.send(
            f'{ctx.author.mention} {config.redTick} Game searching and fetching has been temporarily disabled. For more information see https://www.reddit.com/r/NintendoSwitch/comments/g7w97x/'
        )


class ChatControl(commands.Cog, name='Utility Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.boostChannel = self.bot.get_channel(config.boostChannel)
        self.voiceTextChannel = self.bot.get_channel(config.voiceTextChannel)
        self.voiceTextAccess = self.bot.get_guild(config.nintendoswitch).get_role(config.voiceTextAccess)
        self.SMM2LevelID = re.compile(r'([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})', re.I | re.M)
        self.SMM2LevelPost = re.compile(
            r'Name: ?(\S.*)\n\n?(?:Level )?ID:\s*((?:[0-9a-z]{3}-){2}[0-9a-z]{3})(?:\s+)?\n\n?Style: ?(\S.*)\n\n?(?:Theme: ?(\S.*)\n\n?)?(?:Tags: ?(\S.*)\n\n?)?Difficulty: ?(\S.*)\n\n?Description: ?(\S.*)',
            re.I,
        )
        self.affiliateTags = {
            "*": ["awc"],
            "amazon.*": ["colid", "coliid", "tag", "ascsubtag"],
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
        self.inviteRe = re.compile(
            r'((?:https?:\/\/)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com\/invite)\/[\da-z-]+)', re.I
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:  # If other info than channel (such as mute status), ignore
            return

        if not before.channel:  # User just joined a channel
            await member.add_roles(self.voiceTextAccess)

        elif not after.channel:  # User just left a channel or moved to AFK
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

        # Filter invite links
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
                        if not invite.guild:
                            pass
                        if invite.guild.id in guildWhitelist:
                            continue
                        if 'VERIFIED' in invite.guild.features:
                            continue
                        if 'PARTNERED' in invite.guild.features:
                            continue

                        inviteInfos.append(invite)

                except (discord.NotFound, discord.HTTPException):
                    inviteInfos.append(x)

            if inviteInfos:
                await message.delete()
                await message.channel.send(
                    f':bangbang: {message.author.mention} please do not post invite links to other Discord servers or groups. If you believe the linked server(s) should be whitelisted, contact a moderator',
                    delete_after=10,
                )
                await self.adminChannel.send(
                    f'⚠️ {message.author.mention} has posted a message with one or more invite links in {message.channel.mention} and has been deleted.\nInvite(s): {" | ".join(msgInvites)}'
                )

        # Filter for #mario
        if message.channel.id == config.marioluigiChannel:  # #mario
            if tools.re_match_nonlink(self.SMM2LevelID, message.content):
                await message.delete()
                response = await message.channel.send(
                    f'{config.redTick} <@{message.author.id}> Please do not post Super Mario Maker 2 level codes '
                    f'here. Post in <#{config.smm2Channel}> with the pinned template instead.'
                )

                await response.delete(delay=20)
            return

        # Filter for #smm2-levels
        if message.channel.id == config.smm2Channel:
            if not re.search(self.SMM2LevelID, message.content):
                # We only want to filter posts with a level id
                return

            block = re.search(self.SMM2LevelPost, message.content)
            if not block:
                # No match for a properly formatted level post
                response = await message.channel.send(
                    f'{config.redTick} <@{message.author.id}> Your level is formatted incorrectly, please see the pinned messages for the format. A copy '
                    f'of your message is included and will be deleted shortly. You can resubmit your level at any time.\n\n```{message.content}```'
                )
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
            # #mab_remover is the special sauce that allows users to delete their messages, see on_raw_reaction_add()
            embed.set_author(
                name=f'{str(message.author)} ({message.author.id})',
                icon_url=f'{message.author.avatar_url}#mab_remover_{message.author.id}',
            )
            embed.set_footer(text='The author may react with 🗑️ to delete this message.')

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
                new_message = await message.channel.send(embed=embed)
                await new_message.add_reaction('🗑️')
                await message.delete()

            except discord.errors.Forbidden:
                # Fall back to leaving user text
                logging.error(f'[Filter] Unable to send embed to {message.channel.id}')
            return

        # Filter and clean affiliate links
        # We want to call this last to ensure all above items are complete.
        links = tools.linkRe.finditer(message.content)
        if links:
            contentModified = False
            content = message.content
            for link in links:
                linkModified = False

                try:
                    urlParts = urllib.parse.urlsplit(link[0])
                except ValueError:  # Invalid URL edge case
                    continue

                urlPartsList = list(urlParts)

                query_raw = dict(urllib.parse.parse_qsl(urlPartsList[3]))
                # Make all keynames lowercase in dict, this shouldn't break a website, I hope...
                query = {k.lower(): v for k, v in query_raw.items()}

                # For each domain level of hostname, eg. foo.bar.example => foo.bar.example, bar.example, example
                labels = urlParts.hostname.split(".")
                for i in range(0, len(labels)):
                    domain = ".".join(labels[i - len(labels) :])

                    # Special case: rewrite 'amazon.*/exec/obidos/ASIN/.../' to 'amazon.*/dp/.../'
                    if pathlib.PurePath(domain).match('amazon.*'):
                        match = re.match(r'^/exec/obidos/ASIN/(\w+)/.*$', urlParts.path)
                        if match:
                            linkModified = True
                            urlPartsList[2] = f'/dp/{match.group(1)}'  # 2 = path

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

                if hooks:
                    useHook = hooks[0]
                else:
                    useHook = await message.channel.create_webhook(
                        name=f'mab_{message.channel.id}',
                        reason='No webhooks existed; 1 or more is required for affiliate filtering',
                    )

                async with aiohttp.ClientSession() as session:
                    webhook = Webhook.from_url(useHook.url, adapter=AsyncWebhookAdapter(session))
                    webhook_message = await webhook.send(
                        content=content,
                        username=message.author.display_name,
                        avatar_url=message.author.avatar_url,
                        wait=True,
                    )

                    try:
                        await message.delete()
                    except Exception:
                        pass

                    embed = discord.Embed(
                        description='The above message was automatically reposted by Mecha Bowser to remove an affiliate marketing link. The author may react with 🗑️ to delete these messages.'
                    )

                    # #mab_remover is the special sauce that allows users to delete their messages, see on_raw_reaction_add()
                    icon_url = f'{message.author.avatar_url}#mab_remover_{message.author.id}_{webhook_message.id}'
                    embed.set_footer(text=f'Author: {str(message.author)} ({message.author.id})', icon_url=icon_url)

                    # A seperate message is sent so that the original message has embeds
                    embed_message = await message.channel.send(embed=embed)
                    await embed_message.add_reaction('🗑️')

    # Handle :wastebasket: reactions for user deletions on messages reposed on a user's behalf
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if not payload.member:
            return  # Not in a guild
        if payload.emoji.name != '🗑️':
            return  # Not a :wastebasket: emoji
        if payload.user_id == self.bot.user.id:
            return  # This reaction was added by this bot

        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        embed = None if not message.embeds else message.embeds[0]

        if message.author.id != self.bot.user.id:
            return  # Message is not from the bot
        if not embed:
            return  # Message does not have an embed

        allowed_remover = None
        target_message = None
        # Search for special url tag in footer/author icon urls:
        # ...#mab_remover_{remover} or ..#mab_remover_{remover}_{message}
        for icon_url in [embed.author.icon_url, embed.footer.icon_url]:
            if not icon_url:
                continue  # Location does not have an icon_url

            match = re.search(r'#mab_remover_(\d{15,25})(?:_(\d{15,25}))?$', icon_url)
            if not match:
                continue  # No special url tag here

            allowed_remover = match.group(1)
            target_message = match.group(2)
            break

        if not allowed_remover:
            return  # No special url tag detected
        if str(payload.user_id) != str(allowed_remover):
            return  # Reactor is not the allowed remover
        try:
            if target_message:
                msg = await channel.fetch_message(target_message)
                await msg.delete()

            await message.delete()
        except Exception as e:
            logging.warning(e)
            pass

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
        return await m.delete(delay=5)

    @commands.group(name='slowmode', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    async def _slowmode(self, ctx, duration, channel: typing.Optional[discord.TextChannel]):
        if not channel:
            channel = ctx.channel

        try:
            time, seconds = tools.resolve_duration(duration, include_seconds=True)
            time = tools.humanize_duration(time)
            seconds = int(seconds)
            if seconds < 1:
                return ctx.send(
                    f'{config.redTick} You cannot set the duration to less than one second. If you would like to clear the slowmode, use the `{ctx.prefix}slowmode clear` command'
                )

            elif seconds > 60 * 60 * 6:  # Six hour API limit
                return ctx.send(f'{config.redTick} You cannot set the duration greater than six hours')

        except KeyError:
            return await ctx.send(f'{config.redTick} Invalid duration passed')

        if channel.slowmode_delay == seconds:
            return await ctx.send(f'{config.redTick} The slowmode is already set to {time}')

        await channel.edit(slowmode_delay=seconds, reason=f'{ctx.author} has changed the slowmode delay')
        await channel.send(
            f':stopwatch: This channel now has a **{time}** slowmode in effect. Please be mindful of spam per the server rules'
        )
        if channel.id == ctx.channel.id or tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {channel.mention} now has a {time} slowmode')

    @_slowmode.command(name='clear')
    @commands.has_any_role(config.moderator, config.eh)
    async def _slowmode_clear(self, ctx, channel: typing.Optional[discord.TextChannel]):
        if not channel:
            channel = ctx.channel

        if channel.slowmode_delay == 0:
            return await ctx.send(f'{config.redTick} {channel.mention} is not under a slowmode')

        await channel.edit(slowmode_delay=0, reason=f'{ctx.author} has removed the slowmode delay')
        await channel.send(
            f':stopwatch: Slowmode for this channel is no longer in effect. Please be mindful of spam per the server rules'
        )
        if channel.id == ctx.channel.id or tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        return await ctx.send(f'{config.greenTick} {channel.mention} no longer has slowmode')

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
                desc = (
                    f'Fetched information about {user.mention} from the API because they are not in this server. '
                    'There is little information to display as they have not been recorded joining the server before'
                )

                infractions = mclient.bowser.puns.find({'user': user.id}).count()
                if infractions:
                    desc += f'\n\nUser has {infractions} infraction entr{"y" if infractions == 1 else "ies"}, use `{ctx.prefix}history {user.id}` to view'

                embed = discord.Embed(color=discord.Color(0x18EE1C), description=desc)
                embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
                embed.set_thumbnail(url=user.avatar_url)
                embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'))

                return await ctx.send(embed=embed)  # TODO: Return DB info if it exists as well

        else:
            dbUser = mclient.bowser.users.find_one({'_id': user.id})

        # Member object, loads of info to work with
        messages = mclient.bowser.messages.find({'author': user.id})
        msgCount = 0 if not messages else messages.count()

        desc = (
            f'Fetched user {user.mention}.'
            if inServer
            else (
                f'Fetched information about previous member {user.mention} '
                'from the API because they are not in this server. '
                'Showing last known data from before they left'
            )
        )

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

        lastMsg = (
            'N/a'
            if msgCount == 0
            else datetime.datetime.utcfromtimestamp(
                messages.sort('timestamp', pymongo.DESCENDING)[0]['timestamp']
            ).strftime('%B %d, %Y %H:%M:%S UTC')
        )
        embed.add_field(name='Last message', value=lastMsg, inline=True)
        embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)

        noteDocs = mclient.bowser.puns.find({'user': user.id, 'type': 'note'})
        fieldValue = 'View history to get full details on all notes\n\n'
        if noteDocs.count():
            noteCnt = noteDocs.count()
            noteList = []
            for x in noteDocs.sort('timestamp', pymongo.DESCENDING):
                stamp = datetime.datetime.utcfromtimestamp(x['timestamp']).strftime('`[%m/%d/%y]`')
                noteContent = f'{stamp}: {x["reason"]}'

                fieldLength = 0
                for value in noteList:
                    fieldLength += len(value)
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
            activeStrikes = 0
            totalStrikes = 0
            for pun in punsCol.sort('timestamp', pymongo.DESCENDING):
                if pun['type'] == 'strike':
                    totalStrikes += pun['strike_count']
                    activeStrikes += pun['active_strike_count']

                elif pun['type'] == 'destrike':
                    totalStrikes -= pun['strike_count']

                if puns >= 5:
                    continue

                puns += 1
                stamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%m/%d/%y %H:%M:%S UTC')
                punType = config.punStrs[pun['type']]
                if pun['type'] in ['clear', 'unmute', 'unban', 'unblacklist', 'destrike']:
                    if pun['type'] == 'destrike':
                        punType = f'Removed {pun["strike_count"]} Strike{"s" if pun["strike_count"] > 1 else ""}'

                    punishments += f'- [{stamp}] {punType}\n'

                else:
                    if pun['type'] == 'strike':
                        punType = f'{pun["strike_count"]} Strike{"s" if pun["strike_count"] > 1 else ""}'

                    punishments += f'+ [{stamp}] {punType}\n'

            punishments = (
                f'Showing {puns}/{punsCol.count()} punishment entries. '
                f'For a full history including responsible moderator, active status, and more use `{ctx.prefix}history {user.id}`'
                f'\n```diff\n{punishments}```'
            )

            if totalStrikes:
                embed.description = (
                    embed.description
                    + f'\nUser currently has {activeStrikes} active strike{"s" if activeStrikes != 1 else ""} ({totalStrikes} in total)'
                )

        embed.add_field(name='Punishments', value=punishments, inline=False)
        return await ctx.send(embed=embed)

    @commands.command(name='history')
    async def _history(self, ctx, user: typing.Union[discord.User, int, None] = None):
        if user is None:
            user = ctx.author

        if type(user) == int:
            # User doesn't share the ctx server, fetch it instead
            try:
                user = await self.bot.fetch_user(user)

            except discord.NotFound:
                return await ctx.send(f'{config.redTick} User does not exist')

        if (
            ctx.guild.get_role(config.moderator) not in ctx.author.roles
            and ctx.guild.get_role(config.eh) not in ctx.author.roles
        ):
            self_check = True

            #  If they are not mod and not running on themselves, they do not have permssion.
            if user != ctx.author:
                await ctx.message.delete()
                return await ctx.send(
                    f'{config.redTick} You do not have permission to run this command on other users', delete_after=15
                )

            if ctx.channel.id != config.commandsChannel:
                await ctx.message.delete()
                return await ctx.send(
                    f'{config.redTick} {ctx.author.mention} Please use bot commands in <#{config.commandsChannel}>, not {ctx.channel.mention}',
                    delete_after=15,
                )

        else:
            self_check = False

        db = mclient.bowser.puns
        puns = db.find({'user': user.id, 'type': {'$ne': 'note'}}) if self_check else db.find({'user': user.id})

        deictic_language = {
            'no_punishments': ('User has no punishments on record', 'You have no available punishments on record'),
            'single_inf': (
                'There is **1** infraction record for this user:',
                'You have **1** available infraction record:',
            ),
            'multiple_infs': (
                'There are **{}** infraction records for this user:',
                'You have **{}** available infraction records:',
            ),
            'total_strikes': (
                'User currently has **{}** active strikes (**{}** in total.)\n',
                'You currently have **{}** active strikes (**{}** in total.)\n',
            ),
        }

        if not puns.count():
            return await ctx.channel.send(f'{config.redTick} {deictic_language["no_punishments"][self_check]}')

        else:
            punNames = {
                'strike': '{} Strike{}',
                'destrike': 'Removed {} Strike{}',
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
                'note': 'User note',
            }

            desc = (
                deictic_language['single_inf'][self_check]
                if puns.count() == 1
                else deictic_language['multiple_infs'][self_check].format(puns.count())
            )
            fields = []
            activeStrikes = 0
            totalStrikes = 0
            for pun in puns.sort('timestamp', pymongo.DESCENDING):
                datestamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%b %d, %y %H:%M UTC')
                moderator = ctx.guild.get_member(pun['moderator'])
                if not moderator:
                    moderator = await self.bot.fetch_user(pun['moderator'])

                if pun['type'] == 'strike':
                    activeStrikes += pun['active_strike_count']
                    totalStrikes += pun['strike_count']
                    inf = punNames[pun['type']].format(pun['strike_count'], "s" if pun['strike_count'] > 1 else "")

                elif pun['type'] == 'destrike':
                    totalStrikes -= pun['strike_count']
                    inf = punNames[pun['type']].format(pun['strike_count'], "s" if pun['strike_count'] > 1 else "")

                elif pun['type'] in ['blacklist', 'unblacklist']:
                    inf = punNames[pun['type']].format(pun['context'])

                elif pun['type'] == 'appealdeny':
                    inf = punNames[pun['type']].format(
                        datetime.datetime.utcfromtimestamp(pun['expiry']).strftime('%b. %d, %Y')
                    )

                else:
                    inf = punNames[pun['type']]

                fields.append(
                    {'name': datestamp, 'value': f'**Moderator:** {moderator}\n**Details:** [{inf}] {pun["reason"]}'}
                )

            if totalStrikes:
                desc = deictic_language['total_strikes'][self_check].format(activeStrikes, totalStrikes) + desc

        try:
            channel = ctx.author if self_check else ctx.channel

            if self_check:
                await channel.send(
                    'You requested the following copy of your current infraction history. If you have questions concerning your history,'
                    + f' you may contact the moderation team by sending a DM to our modmail bot, Parakarry (<@{config.parakarry}>)'
                )
                await ctx.message.add_reaction('📬')

            author = {'name': f'{user} | {user.id}', 'icon_url': user.avatar_url}
            await tools.send_paginated_embed(
                self.bot, channel, fields, title='Infraction History', description=desc, color=0x18EE1C, author=author
            )

        except discord.Forbidden:
            if self_check:
                await ctx.send(
                    f'{config.redTick} {ctx.author.mention} I was unable to DM you. Please make sure your DMs are open and try again',
                    delete_after=10,
                )
            else:
                raise

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

        if query:
            query = query.lower()
            tag = db.find_one({'_id': query, 'active': True})

            if not tag:
                return await ctx.send(f'{config.redTick} A tag with that name does not exist', delete_after=10)

            await ctx.message.delete()

            embed = discord.Embed(title=tag['_id'], description=tag['content'])
            embed.set_footer(text=f'Requested by {ctx.author}', icon_url=ctx.author.avatar_url)

            if 'img_main' in tag and tag['img_main']:
                embed.set_image(url=tag['img_main'])
            if 'img_thumb' in tag and tag['img_thumb']:
                embed.set_thumbnail(url=tag['img_thumb'])

            return await ctx.send(embed=embed)

        else:
            await self._tag_list(ctx)

    @_tag.command(name='list', aliases=['search'])
    async def _tag_list(self, ctx, *, search: typing.Optional[str] = ''):
        db = mclient.bowser.tags

        tagList = []
        for tag in db.find({'active': True}):
            description = '' if not 'desc' in tag else tag['desc']
            tagList.append({'name': tag['_id'].lower(), 'desc': description, 'content': tag['content']})

        tagList.sort(key=lambda x: x['name'])

        if not tagList:
            return await ctx.send('{config.redTick} This server has no tags!')

        # Called from the !tag command instead of !tag list, so we print the simple list
        if ctx.invoked_with.lower() in ['tag', 'tags']:

            tags = ', '.join([tag['name'] for tag in tagList])

            embed = discord.Embed(
                title='Tag List',
                description=(
                    f'Here is a list of tags you can access:\n\n> {tags}\n\nType `{ctx.prefix}tag <name>` to request a tag or `{ctx.prefix}tag list` to view tags with their descriptions'
                ),
            )
            return await ctx.send(embed=embed)

        else:  # Complex list
            # If the command is being not being run in commands channel, they must be a mod or helpful user to run it.
            if ctx.channel.id != config.commandsChannel:
                if not (
                    ctx.guild.get_role(config.moderator) in ctx.author.roles
                    or ctx.guild.get_role(config.helpfulUser) in ctx.author.roles
                ):
                    return await ctx.send(
                        f'{config.redTick} {ctx.author.mention} Please use this command in <#{config.commandsChannel}>, not {ctx.channel.mention}',
                        delete_after=15,
                    )

            if search:
                embed_desc = f'Here is a list of tags you can access matching query `{search}`:\n*(Type `{ctx.prefix}tag <name>` to request a tag)*'
            else:
                embed_desc = f'Here is a list of all tags you can access:\n*(Type `{ctx.prefix}tag <name>` to request a tag or `{ctx.prefix}tag {ctx.invoked_with} <search>` to search tags)*'

            if search:
                search = search.lower()
                searchRanks = [0] * len(tagList)  # Init search rankings to 0

                # Search name first
                for i, name in enumerate([tag['name'] for tag in tagList]):
                    if name.startswith(search):
                        searchRanks[i] = 1000
                    elif search in name:
                        searchRanks[i] = 800

                # Search descriptions and tag bodies next
                for i, tag in enumerate(tagList):
                    # add 15 * number of matches in desc
                    searchRanks[i] += tag['desc'].lower().count(search) * 15
                    # add 1 * number of matches in content
                    searchRanks[i] += tag['content'].lower().count(search) * 1

                sort_joined_list = [(searchRanks[i], tagList[i]) for i in range(0, len(tagList))]
                sort_joined_list.sort(key=lambda e: e[0], reverse=True)  # Sort from highest rank to lowest

                matches = list(filter(lambda x: x[0] > 0, sort_joined_list))  # Filter to those with matches

                tagList = [x[1] for x in matches]  # Resolve back to tags

            if tagList:
                longest_name = len(max([tag['name'] for tag in tagList], key=len))
                lines = []

                for tag in tagList:
                    name = tag['name'].ljust(longest_name)
                    desc = '*No description*' if not tag['desc'] else tag['desc']

                    lines.append(f'`{name}` {desc}')

            else:
                lines = ['*No results found*']

            fields = tools.convert_list_to_fields(lines, codeblock=False)
            return await tools.send_paginated_embed(
                self.bot,
                ctx.channel,
                fields,
                owner=ctx.author,
                title='Tag List',
                description=embed_desc,
                page_character_limit=1500,
            )

    @_tag.command(name='edit')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_create(self, ctx, name, *, content):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})
        if name in ['list', 'search', 'edit', 'delete', 'source', 'setdesc', 'setimg']:  # Name blacklist
            return await ctx.send(f'{config.redTick} You cannot use that name for a tag', delete_after=10)

        if tag:
            db.update_one(
                {'_id': tag['_id']},
                {
                    '$push': {'revisions': {str(int(time.time())): {'content': tag['content'], 'user': ctx.author.id}}},
                    '$set': {'content': content, 'active': True},
                },
            )
            msg = f'{config.greenTick} The **{name}** tag has been '
            msg += 'updated' if tag['active'] else 'created'
            await ctx.message.delete()
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

    @_tag.command(name='setdesc')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_setdesc(self, ctx, name, *, content: typing.Optional[str] = ''):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})

        content = ' '.join(content.splitlines())

        if tag:
            db.update_one({'_id': tag['_id']}, {'$set': {'desc': content}})

            status = 'updated' if content else 'cleared'
            await ctx.message.delete()
            return await ctx.send(
                f'{config.greenTick} The **{name}** tag description has been {status}', delete_after=10
            )

        else:
            return await ctx.send(f'{config.redTick} The tag "{name}" does not exist')

    @_tag.command(name='setimg')
    @commands.has_any_role(config.moderator, config.helpfulUser)
    async def _tag_setimg(self, ctx, name, img_type_arg, *, url: typing.Optional[str] = ''):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})

        IMG_TYPES = {
            'main': {'key': 'img_main', 'name': 'main'},
            'thumb': {'key': 'img_thumb', 'name': 'thumbnail'},
            'thumbnail': {'key': 'img_thumb', 'name': 'thumbnail'},
        }

        if img_type_arg.lower() in IMG_TYPES:
            img_type = IMG_TYPES[img_type_arg]
        else:
            return await ctx.send(
                f'{config.redTick} An invalid image type, `{img_type_arg}`, was given. Image type must be: {", ". join(IMG_TYPES.keys())}'
            )

        url = ' '.join(url.splitlines())
        match = tools.linkRe.match(url)
        if url and (
            not match or match.span()[0] != 0
        ):  # If url argument does not match or does not begin with a valid url
            return await ctx.send(f'{config.redTick} An invalid url, `{url}`, was given')

        if tag:
            db.update_one({'_id': tag['_id']}, {'$set': {img_type['key']: url}})

            status = 'updated' if url else 'cleared'
            await ctx.message.delete()
            return await ctx.send(
                f'{config.greenTick} The **{name}** tag\'s {img_type["name"]} image has been {status}', delete_after=10
            )
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
            embed = discord.Embed(title=f'{name} source', description=f'```md\n{tag["content"]}\n```')

            description = '' if not 'desc' in tag else tag['desc']
            img_main = '' if not 'img_main' in tag else tag['img_main']
            img_thumb = '' if not 'img_thumb' in tag else tag['img_thumb']

            embed.add_field(
                name='Description', value='*No description*' if not description else description, inline=True
            )
            embed.add_field(name='Main Image', value='*No URL set*' if not img_main else img_main, inline=True)
            embed.add_field(name='Thumbnail Image', value='*No URL set*' if not img_thumb else img_thumb, inline=True)

            return await ctx.send(embed=embed)

        else:
            return await ctx.send(f'{config.redTick} The tag "{name}" does not exist')

    @commands.command(name='blacklist')
    @commands.has_any_role(config.moderator, config.eh)
    async def _roles_set(
        self,
        ctx,
        member: discord.Member,
        channel: typing.Union[discord.TextChannel, discord.CategoryChannel, str],
        *,
        reason='-No reason specified-',
    ):
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Blacklist reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
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

            elif channel in ['reactions', 'reaction', 'react']:
                context = 'reaction'
                mention = 'reactions'
                reactionsRole = ctx.guild.get_role(config.noReactions)
                if reactionsRole in member.roles:  # Toggle role off
                    await member.remove_roles(reactionsRole)
                    statusText = 'Unblacklisted'

                else:  # Toggle role on
                    await member.add_roles(reactionsRole)
                    statusText = 'Blacklisted'

            elif channel in ['attach', 'attachments', 'embed', 'embeds']:
                context = 'attachment/embed'
                mention = 'attachments/embeds'
                noEmbeds = ctx.guild.get_role(config.noEmbeds)
                if noEmbeds in member.roles:  # Toggle role off
                    await member.remove_roles(noEmbeds)
                    statusText = 'Unblacklisted'

                else:  # Toggle role on
                    await member.add_roles(noEmbeds)
                    statusText = 'Blacklisted'

            else:
                return await ctx.send(f'{config.redTick} You cannot blacklist a user from that function')

        elif channel.id == config.suggestions:
            context = channel.name
            mention = channel.mention + ' channel'
            suggestionsRole = ctx.guild.get_role(config.noSuggestions)
            if suggestionsRole in member.roles:  # Toggle role off
                await member.remove_roles(suggestionsRole)
                statusText = 'Unblacklisted'

            else:  # Toggle role on
                await member.add_roles(suggestionsRole)
                statusText = 'Blacklisted'

        elif channel.id == config.spoilers:
            context = channel.name
            mention = channel.mention + ' channel'
            spoilersRole = ctx.guild.get_role(config.noSpoilers)
            if spoilersRole in member.roles:  # Toggle role off
                await member.remove_roles(spoilersRole)
                statusText = 'Unblacklisted'

            else:  # Toggle role on
                await member.add_roles(spoilersRole)
                statusText = 'Blacklisted'

        elif channel.category_id == config.eventCat:
            context = 'events'
            mention = 'event'
            eventsRole = ctx.guild.get_role(config.noEvents)
            if eventsRole in member.roles:  # Toggle role off
                await member.remove_roles(eventsRole)
                statusText = 'Unblacklisted'

            else:  # Toggle role on
                await member.add_roles(eventsRole)
                statusText = 'Blacklisted'

        else:
            return await ctx.send(f'{config.redTick} You cannot blacklist a user from that channel')

        db = mclient.bowser.puns
        if statusText.lower() == 'blacklisted':
            docID = await tools.issue_pun(member.id, ctx.author.id, 'blacklist', reason, context=context)

        else:
            db.find_one_and_update(
                {'user': member.id, 'type': 'blacklist', 'active': True, 'context': context},
                {'$set': {'active': False}},
            )
            docID = await tools.issue_pun(
                member.id, ctx.author.id, 'unblacklist', reason, active=False, context=context
            )

        await tools.send_modlog(
            self.bot,
            self.modLogs,
            statusText.lower()[:-2],
            docID,
            reason,
            user=member,
            moderator=ctx.author,
            extra_author=context,
            public=True,
        )

        try:
            statusText = 'blacklist' if statusText == 'Blacklisted' else 'unblacklist'
            await member.send(tools.format_pundm(statusText, reason, ctx.author, mention))

        except (discord.Forbidden, AttributeError):  # User has DMs off, or cannot send to Obj
            pass

        if tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} has been {statusText.lower()}ed from {mention}')

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(
                f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(
                f'{config.redTick} You are using that command too fast, try again in a few seconds', delete_after=15
            )

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(
                f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command.', delete_after=15)

        else:
            await ctx.send(
                f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.',
                delete_after=15,
            )
            raise error


class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.muteRole = self.bot.get_guild(config.nintendoswitch).get_role(config.mute)
        self.messages = {}

    @commands.Cog.listener()
    async def on_message(self, message):
        self.messages[message.channel.id].append(
            {'user': message.author.id, 'content': message.content, 'id': message.id}
        )

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
