import asyncio
import collections
import logging
import re
import time
import typing
from datetime import datetime, timezone

import config  # type: ignore
import discord
import pymongo
from discord.ext import commands, tasks

import tools  # type: ignore


startTime = int(time.time())
mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)


class MainEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        try:
            self.bot.load_extension('tools')
            self.bot.load_extension('modules.moderation')
            self.bot.load_extension('modules.utility')
            self.bot.load_extension('modules.statistics')
            self.bot.load_extension('modules.social')
            self.bot.load_extension('modules.games')
            try:  # Private submodule extensions
                self.bot.load_extension('private.automod')
            except commands.errors.ExtensionNotFound:
                logging.error('[Core] Unable to load one or more private modules, are you missing the submodule?')

            self.sanitize_eud.start()  # pylint: disable=no-member

        except discord.ext.commands.errors.ExtensionAlreadyLoaded:
            pass

        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.debugChannel = self.bot.get_channel(config.debugChannel)
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.invites = {}

        # Automod is hard coded to this guild, so to reduce confusion, we only init configured guild.
        guild_db = mclient.bowser.guilds
        guild = guild_db.find_one({'_id': config.nintendoswitch})

        if not guild:
            guild_db.insert(
                {
                    "_id": config.nintendoswitch,
                    "inviteWhitelist": [config.nintendoswitch],
                    "whitelist": [],
                    "blacklist": [],
                }
            )

    def cog_unload(self):
        self.sanitize_eud.cancel()  # pylint: disable=no-member

    @tasks.loop(hours=24)
    async def sanitize_eud(self):
        logging.info('[Core] Starting sanitzation of old EUD')
        msgDB = mclient.bowser.messages
        msgDB.update_many(
            {
                'timestamp': {"$lte": time.time() - (86400 * 365)},
                'sanitized': False,
            },  # Store message data upto 1 year old
            {"$set": {'content': None, 'sanitized': True}},
        )

        logging.info('[Core] Finished sanitzation of old EUD')

    @commands.command(name='ping')
    async def _ping(self, ctx):
        initiated = ctx.message.created_at
        msg = await ctx.send('Evaluating...')
        roundtrip = (msg.created_at - initiated).total_seconds() * 1000

        database_start = time.time()
        mclient.bowser.command('ping')
        database = (time.time() - database_start) * 1000

        websocket = self.bot.latency * 1000

        return await msg.edit(
            content=(
                'Pong! Latency: **Roundtrip** `{:1.0f}ms`, **Websocket** `{:1.0f}ms`, **Database** `{:1.0f}ms`'.format(
                    roundtrip, websocket, database
                )
            )
        )

    @commands.Cog.listener()
    async def on_resume(self):
        logging.warning('[Main] The bot has been resumed on Discord')

    @commands.Cog.listener()
    async def on_member_join(self, member):
        db = mclient.bowser.users
        doc = db.find_one({'_id': member.id})
        roleList = []
        restored = False

        if not doc:
            await tools.store_user(member)
            doc = db.find_one({'_id': member.id})

        else:
            db.update_one(
                {'_id': member.id},
                {'$push': {'joins': (datetime.utcnow() - datetime.utcfromtimestamp(0)).total_seconds()}},
            )

        new = (
            ':new: ' if (datetime.now(tz=timezone.utc) - member.created_at).total_seconds() <= 60 * 60 * 24 * 14 else ''
        )  # Two weeks

        # log = f':inbox_tray: {new} User **{str(member)}** ({member.id}) joined'

        embed = discord.Embed(color=0x417505, timestamp=datetime.utcnow())
        embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar.url)
        created_at = member.created_at.strftime(f'{new}%B %d, %Y %H:%M:%S UTC')
        created_at += '' if not new else '\n' + tools.humanize_duration(member.created_at)
        embed.add_field(name='Created at', value=created_at)
        embed.add_field(name='Mention', value=f'<@{member.id}>')

        await self.serverLogs.send(':inbox_tray: User joined', embed=embed)

        if doc and doc['roles']:
            for x in doc['roles']:
                if x == member.guild.id:
                    continue

                restored = True
                role = member.guild.get_role(x)
                if role:
                    roleList.append(role)

            await member.edit(roles=roleList, reason='Automatic role restore action')

        if restored:
            # roleText = ', '.split(x.name for x in roleList)

            # logRestore = f':shield: Roles have been restored for returning member **{str(member)}** ({member.id}):\n{roleText}'
            punTypes = {
                'mute': 'Mute',
                'blacklist': 'Channel Blacklist ({})',
            }
            puns = mclient.bowser.puns.find({'user': member.id, 'active': True})
            restoredPuns = []
            if puns.count():
                for x in puns:
                    if x['type'] == 'blacklist':
                        restoredPuns.append(punTypes[x['type']].format(x['context']))

                    elif x['type'] in ['strike', 'kick', 'ban']:
                        continue  # These are not punishments being "restored", instead only status is being tracked

                    elif x['type'] == 'mute':
                        if (
                            x['expiry'] < time.time()
                        ):  # If the member is rejoining after mute has expired, the task has already quit. Restart it
                            mod = self.bot.get_cog('Moderation Commands')
                            await mod.expire_actions(x['_id'], member.guild.id)

                        restoredPuns.append(punTypes[x['type']])

                    else:
                        restoredPuns.append(punTypes[x['type']])

            embed = discord.Embed(color=0x4A90E2, timestamp=datetime.utcnow())
            embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar.url)
            embed.add_field(name='Restored roles', value=', '.join(x.name for x in roleList))
            if restoredPuns:
                embed.add_field(name='Restored punishments', value=', '.join(restoredPuns))
            embed.add_field(name='Mention', value=f'<@{member.id}>')
            await self.serverLogs.send(':shield: Member restored', embed=embed)

        if mclient.bowser.puns.count_documents(
            {'user': member.id, 'active': True, 'type': {'$in': ['mute', 'strike', 'blacklist']}}
        ):
            activeHist = []
            strikes = 0
            for pun in mclient.bowser.puns.find(
                {'user': member.id, 'active': True, 'type': {'$in': ['mute', 'strike', 'blacklist']}}
            ):
                if pun['type'] == 'strike':
                    strikes += pun['active_strike_count']

                elif pun['type'] == 'mute':
                    activeHist.append('Mute')

                elif pun['type'] == 'blacklist':
                    activeHist.append(f'Blacklist ({pun["context"]})')

            if strikes:
                activeHist.append(f'{strikes} Strike{"s" if strikes > 1 else ""}')

            await self.adminChannel.send(
                f':grey_exclamation: **{member}** ({member.id}) has returned to the server after leaving with the following active punishments:\n{", ".join(activeHist)}'
            )

        if (
            'migrate_unnotified' in doc.keys() and doc['migrate_unnotified'] == True
        ):  # Migration of warnings to strikes for returning members
            for pun in mclient.bowser.puns.find(
                {'active': True, 'type': {'$in': ['tier1', 'tier2', 'tier3']}, 'user': member.id}
            ):  # Should only be one, it's mutually exclusive
                strikeCount = int(pun['type'][-1:]) * 4

                mclient.bowser.puns.update_one({'_id': pun['_id']}, {'$set': {'active': False}})
                docID = await tools.issue_pun(
                    member.id,
                    self.bot.user.id,
                    'strike',
                    f'[Migrated] {pun["reason"]}',
                    strike_count=strikeCount,
                    context='strike-migration',
                    public=False,
                )
                db.update_one(
                    {'_id': member.id},
                    {'$set': {'migrate_unnotified': False, 'strike_check': time.time() + (60 * 60 * 24 * 7)}},
                )  # Setting the next expiry check time
                mod = self.bot.get_cog('Moderation Commands')
                await mod.expire_actions(docID, member.guild.id)

                explanation = (
                    'Hello there **{}**,\nI am letting you know of a change in status for your active level {} warning issued on {}.\n\n'
                    'The **/r/NintendoSwitch** Discord server is moving to a strike-based system for infractions. Here is what you need to know:\n'
                    '\* Your warning level will be converted to **{}** strikes.\n'
                    '\* __Your strikes will decay at a equivalent rate as warnings previously did__. Each warning tier is equivalent to four strikes, where one strike decays once per week instead of one warn level per four weeks\n'
                    '\* You will no longer have any permission restrictions you previously had with this warning. Moderators will instead restrict features as needed to enforce the rules on a case-by-case basis.\n\n'
                    'Strikes will allow the moderation team to weigh rule-breaking behavior better and serve as a reminder to users who may need to review our rules. You may also now view your infraction history '
                    'by using the `!history` command in <#{}>. Please feel free to send a modmail to @Parakarry (<@{}>) if you have any questions or concerns.'
                ).format(
                    str(member),  # Username
                    pun['type'][-1:],  # Tier type
                    datetime.utcfromtimestamp(pun['timestamp']).strftime('%B %d, %Y'),  # Date of warn
                    strikeCount,  # How many strikes will replace tier,
                    config.commandsChannel,  # Commands channel can only be used for the command
                    config.parakarry,  # Parakarry mention for DM
                )

                try:
                    await member.send(explanation)

                except:
                    pass

        # After everything is done, if they don't have an active mute and the mute role, go ahead and remove it. This
        # most likely happens if their mute was marked as false postive after they left the server.
        active_mutes = mclient.bowser.puns.find_one({'user': member.id, 'active': True, 'type': 'mute'})
        muteRole = self.bot.get_guild(config.nintendoswitch).get_role(config.mute)

        if not active_mutes and muteRole in member.roles:
            await member.remove_roles(muteRole, reason='Orphaned mute role removed')

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        # log = f':outbox_tray: User **{str(member)}** ({member.id}) left'
        db = mclient.bowser.puns
        puns = db.find({'user': member.id, 'active': True, 'type': {'$in': ['strike', 'mute', 'blacklist']}})

        mclient.bowser.users.update_one(
            {'_id': member.id},
            {'$push': {'leaves': (datetime.utcnow() - datetime.utcfromtimestamp(0)).total_seconds()}},
        )
        if puns.count():
            embed = discord.Embed(
                description=f'{member} ({member.id}) left the server\n\n:warning: __**User had active punishments**__ :warning:',
                color=0xD62E44,
                timestamp=datetime.utcnow(),
            )
            punishments = []
            for x in puns:
                punishments.append(config.punStrs[x['type']])

            punComma = ', '.join(punishments)
            embed.add_field(name='Punishment types', value=punComma)

            punCode = '\n'.join(punishments)
            await self.adminChannel.send(
                f':warning: **{member}** ({member.id}) left the server with active punishments. See logs for more details\n```{punCode}```'
            )

        else:
            embed = discord.Embed(color=0x8B572A, timestamp=datetime.utcnow())

        embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar.url)
        embed.add_field(name='Mention', value=f'<@{member.id}>')
        await self.serverLogs.send(':outbox_tray: User left', embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if guild.id != config.nintendoswitch:
            return

        db = mclient.bowser.puns
        await asyncio.sleep(10)  # Wait 10 seconds to allow audit log to update
        if not db.find_one({'user': user.id, 'type': 'ban', 'active': True, 'timestamp': {'$gt': time.time() - 60}}):
            # Manual ban
            audited = None
            async for entry in guild.audit_logs(action=discord.AuditLogAction.ban):
                if entry.target == user:
                    audited = entry
                    break

            if audited:
                reason = audited.reason or '-No reason specified-'
                docID = await tools.issue_pun(audited.target.id, audited.user.id, 'ban', reason)

                await tools.send_modlog(
                    self.bot, self.modLogs, 'ban', docID, reason, user=user, moderator=audited.user, public=True
                )

        embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.utcnow())
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.avatar.url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')

        await self.serverLogs.send(':rotating_light: User banned', embed=embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if guild.id != config.nintendoswitch:
            return

        db = mclient.bowser.puns
        if not db.find_one({'user': user.id, 'type': 'unban', 'timestamp': {'$gt': time.time() - 60}}):
            # Manual unban

            audited = None
            async for entry in guild.audit_logs(action=discord.AuditLogAction.unban):
                if entry.target == user:
                    audited = entry
                    break

            if audited:
                reason = (
                    "Ban appeal accepted"
                    if audited.user.id == config.parakarry
                    else audited.reason or '-No reason specified-'
                )
                docID = await tools.issue_pun(audited.target.id, audited.user.id, 'unban', reason, active=False)
                db.update_one({'user': audited.target.id, 'type': 'ban', 'active': True}, {'$set': {'active': False}})

                await tools.send_modlog(
                    self.bot, self.modLogs, 'unban', docID, reason, user=user, moderator=audited.user, public=True
                )

        embed = discord.Embed(color=discord.Color(0x88FF00), timestamp=datetime.utcnow())
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.avatar.url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')

        await self.serverLogs.send(':triangular_flag_on_post: User unbanned', embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id:
            return

        if message.channel.type not in [discord.ChannelType.text, discord.ChannelType.news]:
            logging.debug(f'Discarding non guild message {message.channel.type} {message.id}')
            return

        db = mclient.bowser.messages
        timestamp = int(time.time())
        db.insert_one(
            {
                '_id': message.id,
                'author': message.author.id,
                'guild': message.guild.id,
                'channel': message.channel.id,
                'content': message.content,
                'timestamp': timestamp,
                'sanitized': False,
            }
        )

        await self.bot.process_commands(message)  # Allow commands to fire
        return

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):  # TODO: Work with archives channel attribute to list channels
        if messages[0].channel.type not in [discord.ChannelType.text, discord.ChannelType.news]:
            logging.debug(f'Discarding non guild bulk delete {messages[0].channel.type}  {messages[0].id}')
            return

        await asyncio.sleep(10)  # Give chance for clean command to finish and discord to process delete
        db = mclient.bowser.archive
        checkStamp = int(
            time.time() - 600
        )  # Rate limiting, instability, and being just slow to fire are other factors that could delay the event
        archives = db.find({'timestamp': {'$gt': checkStamp}})
        if archives:  # If the bulk delete is the result of us, exit
            for x in archives:
                if messages[0].id in x['messages']:
                    return

        archiveID = await tools.message_archive(messages)

        # log = f':printer: New message archive has been generated, view it at {config.baseUrl}/archive/{archiveID}'
        embed = discord.Embed(
            description=f'Archive URL: {config.baseUrl}/logs/{archiveID}',
            color=0xF5A623,
            timestamp=datetime.utcnow(),
        )
        return await self.serverLogs.send(':printer: New message archive generated', embed=embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.cached_message:
            if payload.cached_message.type != discord.MessageType.default or payload.cached_message.author.bot:
                return  # No system messages

            if not payload.cached_message.content and not payload.cached_message.attachments:
                return  # Blank or null content (could be embed)

            user = payload.cached_message.author
            jump_url = payload.cached_message.jump_url
            content = payload.cached_message.content if payload.cached_message.content else '-No message content-'

        else:
            # Message is not in ram cache, pull from DB or ignore if missing
            db = mclient.bowser.messages
            dbMessage = db.find_one({'_id': payload.message_id, 'channel': payload.channel_id})
            if not dbMessage:
                logging.warning(
                    f'[Core] Missing message metadata for deletion of {payload.channel_id}/{payload.message_id}'
                )
                return

            user = await self.bot.fetch_user(dbMessage['author'])
            jump_url = f'https://discord.com/channels/{dbMessage["guild"]}/{dbMessage["channel"]}/{dbMessage["_id"]}'
            content = dbMessage['content'] or '-No saved copy of message content available-'

        embed = discord.Embed(
            description=f'[Jump to message]({jump_url})\n{content}',
            color=0xF8E71C,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(name=f'{str(user)} ({user.id})', icon_url=user.avatar.url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')
        if payload.cached_message and len(payload.cached_message.attachments) == 1:
            embed.set_image(url=payload.cached_message.attachments[0].proxy_url)

        elif payload.cached_message and len(payload.cached_message.attachments) > 1:
            # More than one attachment, use fields
            attachments = [x.proxy_url for x in payload.cached_message.attachments]
            for a in range(len(attachments)):
                embed.add_field(name=f'Attachment {a + 1}', value=attachments[a])

        if len(content) > 1950:  # Too long to safely add jump to message in desc, use field
            embed.description = content
            embed.add_field(name='Jump', value=f'[Jump to message]({jump_url})')

        await self.serverLogs.send(f':wastebasket: Message deleted in <#{payload.channel_id}>', embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.channel.type not in [discord.ChannelType.text, discord.ChannelType.news]:
            logging.debug(f'Discarding non guild edit {before.channel.type} {before.id}')
            return

        if before.content == after.content or before.author.bot:
            return

        if before.type != discord.MessageType.default:
            return  # No system messages

        if not after.content or not before.content:
            return  # Blank or null content (could be embed)

        # log = f':pencil: Message by **{str(before.author)}** ({before.author.id}) in <#{before.channel.id}> edited:\n'
        # editedMsg = f'__Before:__ {before.clean_content}\n\n__After:__ {after.clean_content}'
        # fullLog = log + editedMsg if (len(log) + len(editedMsg)) < 2000 else log + 'Message exceeds character limit, ' \
        #    f'view at {config.baseUrl}/archive/{await utils.message_archive([before, after], True)}'

        if len(before.content) <= 1024 and len(after.content) <= 1024:
            embed = discord.Embed(
                description=f'[Jump to message]({before.jump_url})',
                color=0xF8E71C,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(name='Before', value=before.content, inline=False)
            embed.add_field(name='After', value=after.content, inline=False)

        else:
            embed = discord.Embed(
                description=f'[Jump to message]({before.jump_url})\nMessage diff exceeds character limit, view at {config.baseUrl}/logs/{await tools.message_archive([before, after], True)}',
                color=0xF8E71C,
                timestamp=datetime.utcnow(),
            )

        embed.set_author(name=f'{str(before.author)} ({before.author.id})', icon_url=before.author.avatar.url)
        embed.add_field(name='Mention', value=f'<@{before.author.id}>')

        await self.serverLogs.send(f':pencil: Message edited in <#{before.channel.id}>', embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        userCol = mclient.bowser.users
        if before.nick != after.nick:
            if not before.nick:
                before_name = before.name

            else:
                before_name = discord.utils.escape_markdown(before.nick)

            if not after.nick:
                after_name = after.name

            else:
                after_name = discord.utils.escape_markdown(after.nick)

            embed = discord.Embed(color=0x9535EC, timestamp=datetime.utcnow())
            embed.set_author(name=f'{before} ({before.id})', icon_url=before.avatar.url)
            embed.add_field(name='Before', value=before_name, inline=False)
            embed.add_field(name='After', value=after_name, inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s nickname updated', embed=embed)

        if before.roles != after.roles:
            roleList = []
            roleStr = []
            for x in after.roles:
                if x.id == before.guild.id:
                    continue

                if not x.managed:
                    roleList.append(x.id)
                roleStr.append(x.name)

            userCol.update_one({'_id': before.id}, {'$set': {'roles': roleList}})

            beforeCounter = collections.Counter(before.roles)
            afterCounter = collections.Counter(after.roles)

            rolesRemoved = list(map(lambda x: x.name, beforeCounter - afterCounter))
            rolesAdded = list(map(lambda x: x.name, afterCounter - beforeCounter))
            roleStr = ['*No roles*'] if not roleStr else roleStr

            if rolesRemoved or rolesAdded:  # nop if no change, e.g. role moves in list
                embed = discord.Embed(color=0x9535EC, timestamp=datetime.utcnow())
                embed.set_author(name=f'{before} ({before.id})', icon_url=before.avatar.url)

                if rolesRemoved:
                    embed.add_field(
                        name=f'Role{"" if len(rolesRemoved) == 1 else "s"} Removed (-)', value=', '.join(rolesRemoved)
                    )

                if rolesAdded:
                    embed.add_field(
                        name=f'Role{"" if len(rolesAdded) == 1 else "s"} Added (+)', value=', '.join(rolesAdded)
                    )

                embed.add_field(
                    name=f'Current Role{"" if len(roleStr) == 1 else "s"}',
                    value=', '.join(n for n in reversed(roleStr)),
                    inline=False,
                )
                embed.add_field(name='Mention', value=f'<@{before.id}>')
                await self.serverLogs.send(':closed_lock_with_key: User\'s roles updated', embed=embed)

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        before_name = discord.utils.escape_markdown(before.name)
        after_name = discord.utils.escape_markdown(after.name)
        if before.name != after.name:
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.utcnow())
            embed.set_author(name=f'{after} ({after.id})', icon_url=after.avatar.url)
            embed.add_field(name='Before', value=str(before), inline=False)
            embed.add_field(name='After', value=str(after), inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s name updated', embed=embed)

        elif before.discriminator != after.discriminator:
            # Really only case this would be called, and not username (i.e. discrim reroll after name change)
            # is when nitro runs out with a custom discriminator set
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.utcnow())
            embed.set_author(name=f'{after} ({after.id})', icon_url=after.avatar.url)
            embed.add_field(name='Before', value=before_name, inline=False)
            embed.add_field(name='After', value=after_name, inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s name updated', embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        db = mclient.bowser.users
        for user in db.find({'roles': {'$in': [role.id]}}):
            storedRoles = user['roles']
            storedRoles.remove(role.id)
            db.update_one({'_id': user['_id']}, {'$set': {'roles': storedRoles}})

    @commands.command(name='update')
    @commands.is_owner()
    async def _update(self, ctx, sub, *args):
        if sub == 'pfp':
            if not ctx.message.attachments:
                return await ctx.send(':warning: An attachment to change the picture to was not provided')

            else:
                attachment = await ctx.message.attachments[0].read()
                await self.bot.user.edit(avatar=attachment)

            return await ctx.send('Done.')

        elif sub == 'name':
            username = ''
            for x in args:
                username += f'{x} '

            if len(username[:-1]) >= 32:
                return await ctx.send(':warning: That username is too long.')

            await self.bot.user.edit(username=username)

        elif sub == 'servermsgcache':
            funcStart = time.time()
            logging.info('[Core] Starting db message sync')
            await ctx.send(
                'Starting syncronization of db for all messages in server. This will take a conciderable amount of time.'
            )
            for channel in ctx.guild.channels:
                if channel.type != discord.ChannelType.text:
                    continue

                await ctx.send(f'Starting syncronization for <#{channel.id}>')

                try:
                    x, y = await self.store_message_cache(channel)
                    await ctx.send(
                        f'Syncronized <#{channel.id}>. Processed {x} messages and recorded meta data for {y} messages'
                    )

                except (discord.Forbidden, discord.HTTPException):
                    await ctx.send(f'Failed to syncronize <#{channel.id}>')

            timeToComplete = tools.humanize_duration(tools.resolve_duration(f'{int(time.time() - funcStart)}s'))
            return await ctx.send(f'<@{ctx.author.id}> Syncronization completed. Took {timeToComplete}')

        else:
            return await ctx.send('Invalid sub command')

    @commands.command(name='pundb')
    @commands.is_owner()
    async def _pundb(
        self, ctx, _type, user, moderator, strTime, active: typing.Optional[bool], *, reason='-No reason specified-'
    ):
        date = datetime.strptime(strTime, '%m/%d/%y')
        expiry = None if not active else int(date.timestamp() + (60 * 60 * 24 * 30))
        await tools.issue_pun(int(user), int(moderator), _type, reason, expiry, active, 'old', date.timestamp())
        await ctx.send(f'{config.greenTick} Done')

    @commands.command(name='shutdown')
    @commands.is_owner()
    async def _shutdown(self, ctx):
        await ctx.send('Closing connection to discord and shutting down')
        return await self.bot.close()

    async def store_message_cache(self, channel):
        # users = mclient.bowser.users
        db = mclient.bowser.messages
        x = 0
        y = 0
        async for message in channel.history(limit=None):
            x += 1
            if message.author.bot:
                continue

            msg = db.find_one({'_id': message.id})
            if not msg:
                y += 1
                db.insert_one(
                    {
                        '_id': message.id,
                        'author': message.author.id,
                        'guild': message.guild.id,
                        'channel': message.channel.id,
                        'timestamp': int(message.created_at.timestamp()),
                    }
                )
                # if not users.find_one({'_id': message.author.id}):
                # users.insert_one({'_id': message.author.id, 'roles': []})

            # else:
            # if not users.find_one({'_id': message.author.id}):
            # users.insert_one({'_id': message.author.id, 'roles': []})

        return x, y


def setup(bot):
    bot.add_cog(MainEvents(bot))
    logging.info('[Extension] Main module loaded')


def teardown(bot):
    bot.remove_cog('MainEvents')
    logging.info('[Extension] Main module unloaded')
