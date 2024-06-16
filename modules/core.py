import asyncio
import collections
import logging
import time
import typing
from datetime import datetime, timezone

import config  # type: ignore
import discord
import pymongo
from discord import app_commands
from discord.ext import commands, tasks

import tools  # type: ignore


startTime = int(time.time())
mclient = pymongo.MongoClient(config.mongoURI)


class MainEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await self.bot.load_extension('tools')
            await self.bot.load_extension('modules.moderation')
            await self.bot.load_extension('modules.utility')
            await self.bot.load_extension('modules.statistics')
            await self.bot.load_extension('modules.social')
            await self.bot.load_extension('modules.games')
            try:  # Private submodule extensions
                await self.bot.load_extension('private.automod')
            except commands.errors.ExtensionNotFound:
                logging.error('[Core] Unable to load one or more private modules, are you missing the submodule?')

        except discord.ext.commands.errors.ExtensionAlreadyLoaded:
            pass

        # self.sanitize_eud.start()  # pylint: disable=no-member

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

    #    def cog_unload(self):
    #        self.sanitize_eud.cancel()  # pylint: disable=no-member

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

    @app_commands.command(
        name='ping', description='Checks that the bot is responding normally and shows various latency values'
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    async def _ping(self, interaction: discord.Interaction):
        initiated = interaction.created_at
        await interaction.response.send_message('Evaluating...')
        msg = await interaction.original_response()  # response.send_message does not return a discord.Message
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

    @app_commands.command(name='treesync')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _tree_sync(self, interaction: discord.Interaction):
        '''
        The purpose of this command is to allow us to manually resync the tree and command IDs if they may have changed say from a cog load/unload.

        This may be eventually integrated into a custom jishaku cog to overwrite it's `load`, `reload`, `unload` commands to implement sync and id fetching.
        '''

        await interaction.response.defer(ephemeral=True)

        remote = await self.bot.tree.sync(guild=interaction.guild)
        local = self.bot.tree.get_commands(guild=interaction.guild)
        for rc, lc in zip(remote, local): # We are pulling command IDs from server-side, then storing the mentions
            lc.extras['id'] = rc.id

        await interaction.followup.send(f'Synced **{len(remote)}** guilds commands')

    @commands.Cog.listener()
    async def on_resume(self):
        logging.warning('[Main] The bot has been resumed on Discord')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:  # If other info than channel (such as mute status), ignore
            return

        # Add to database
        mclient.bowser.users.update_one(
            {'_id': member.id},
            {
                '$push': {
                    'voiceHistory': {
                        'before': before.channel.id if before.channel else None,
                        'after': after.channel.id if after.channel else None,
                        'timestamp': int(datetime.now(tz=timezone.utc).timestamp()),
                    }
                }
            },
        )

        embed = discord.Embed(color=0x65A398, timestamp=datetime.now(tz=timezone.utc))
        embed.set_author(name=f'{member} ({member.id})', icon_url=member.display_avatar.url)

        if not before.channel:  # User just joined
            embed.add_field(name='→ Connected to', value=after.channel.mention, inline=True)

        elif not after.channel:  # User just left a channel
            embed.add_field(name='← Disconnected from', value=before.channel.mention, inline=True)

        else:  # Changed channel
            embed.add_field(name='Moved from', value=before.channel.mention, inline=True)
            embed.add_field(name='→ to', value=after.channel.mention, inline=True)

        embed.add_field(name='Mention', value=f'<@{member.id}>', inline=False)

        await self.serverLogs.send(':microphone2: User changed voice channel', embed=embed)

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
                {'$push': {'joins': int(datetime.now(tz=timezone.utc).timestamp())}},
            )

        new = (
            ':new: ' if (datetime.now(tz=timezone.utc) - member.created_at).total_seconds() <= 60 * 60 * 24 * 14 else ''
        )  # Two weeks

        embed = discord.Embed(color=0x417505, timestamp=datetime.now(tz=timezone.utc))
        embed.set_author(name=f'{member} ({member.id})', icon_url=member.display_avatar.url)
        created_at = f'{new} <t:{int(member.created_at.timestamp())}:f>'
        created_at += '' if not new else f'\n<t:{int(member.created_at.timestamp())}:R>'
        embed.add_field(name='Created at', value=created_at)
        embed.add_field(name='Mention', value=f'<@{member.id}>')

        await self.serverLogs.send(':inbox_tray: User joined', embed=embed)

        needsRestore = False
        hierarchyFails = []
        if doc and doc['roles']:
            myTop = member.guild.me.top_role
            for x in doc['roles']:
                if x == member.guild.id:
                    continue

                needsRestore = True
                role = member.guild.get_role(x)
                if role:
                    # Checks if role exists
                    if myTop > role:
                        roleList.append(role)

                    else:
                        hierarchyFails.append(role)

            await member.edit(roles=roleList, reason='Automatic role restore action')

        punDB = mclient.bowser.puns
        if needsRestore or punDB.find_one({'user': member.id, 'type': 'mute', 'active': True}):
            punTypes = {
                'mute': 'Mute',
                'blacklist': 'Channel Blacklist ({})',
            }
            puns = punDB.find({'user': member.id, 'active': True})
            restoredPuns = []
            if puns.count():
                for x in puns:
                    if x['type'] == 'blacklist':
                        restoredPuns.append(punTypes[x['type']].format(x['context']))

                    elif x['type'] in ['strike', 'kick', 'ban', 'appealdeny']:
                        continue  # These are not punishments being "restored", instead only status is being tracked

                    elif x['type'] == 'mute':
                        if (
                            x['expiry'] < time.time()
                        ):  # If the member is rejoining after mute has expired, the task has already quit. Restart it
                            mod = self.bot.get_cog('Moderation Commands')
                            await mod.expire_actions(x['_id'], member.guild.id)

                        else:
                            # The member rejoined while a mute is still active, reapply the chat timeout.
                            # We want to make sure if the expiry was modified while they were not in the
                            # server that the correct timeout is applied
                            await member.edit(
                                timed_out_until=datetime.fromtimestamp(x['expiry'], tz=timezone.utc),
                                reason='Reapplying timeout after user rejoined',
                            )

                            restoredPuns.append(punTypes[x['type']])

                    elif x['type'] in ['tier1', 'tier2', 'tier3']:
                        # We don't want to handle this, these will be converted to strikes further on
                        pass

                    else:
                        restoredPuns.append(punTypes[x['type']])

            embed = discord.Embed(color=0x4A90E2, timestamp=datetime.now(tz=timezone.utc))
            embed.set_author(name=f'{member} ({member.id})', icon_url=member.display_avatar.url)
            embed.add_field(name='Restored roles', value=', '.join(x.name for x in roleList) or 'None')
            if hierarchyFails:
                embed.description = (
                    f':warning: Failed to reassign some or all roles due to missing permissions:\n> '
                    + ', '.join(x.name for x in hierarchyFails)
                )
                await self.adminChannel.send(
                    f':warning: **{member}** ({member.id}) rejoined the server, but I failed to restore some or all roles due to missing permissions: '
                    + ', '.join(x.name for x in hierarchyFails)
                )
            if restoredPuns:
                embed.add_field(name='Restored punishments', value=', '.join(restoredPuns))
            embed.add_field(name='Mention', value=f'<@{member.id}>')
            await self.serverLogs.send(':shield: Member restored', embed=embed)

        if punDB.count_documents({'user': member.id, 'active': True, 'type': {'$in': ['mute', 'strike', 'blacklist']}}):
            activeHist = []
            strikes = 0
            for pun in punDB.find(
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
                f':grey_exclamation: **{member}** ({member.id}) rejoined the server after leaving with the following active punishments:\n{", ".join(activeHist)}'
            )

        if (
            'migrate_unnotified' in doc.keys() and doc['migrate_unnotified'] == True
        ):  # Migration of warnings to strikes for returning members
            for pun in punDB.find(
                {'active': True, 'type': {'$in': ['tier1', 'tier2', 'tier3']}, 'user': member.id}
            ):  # Should only be one, it's mutually exclusive
                strikeCount = int(pun['type'][-1:]) * 4

                punDB.update_one({'_id': pun['_id']}, {'$set': {'active': False}})

                explanation = (
                    'Hello there **{}**,\nI am letting you know of a change in status for your active level {} warning issued on {}.\n\n'
                    'The **/r/NintendoSwitch** Discord server is moving to a strike-based system for infractions. Here is what you need to know:\n'
                    '- Your warning level will be converted to **{}** strikes.\n'
                    '- __Your strikes will decay at a equivalent rate as warnings previously did__. Each warning tier is equivalent to four strikes, where one strike decays once per week instead of one warn level per four weeks\n'
                    '- You will no longer have any permission restrictions you previously had with this warning. Moderators will instead restrict features as needed to enforce the rules on a case-by-case basis.\n\n'
                    'Strikes will allow the moderation team to weigh rule-breaking behavior better and serve as a reminder to users who may need to review our rules. You may also now view your infraction history '
                    'by using the `!history` command in <#{}>. Please feel free to send a modmail to @Parakarry (<@{}>) if you have any questions or concerns.'
                ).format(
                    str(member),  # Username
                    pun['type'][-1:],  # Tier type
                    f'<t:{int(pun["timestamp"])}:D>',  # Date of warn
                    strikeCount,  # How many strikes will replace tier,
                    config.commandsChannel,  # Commands channel can only be used for the command
                    config.parakarry,  # Parakarry mention for DM
                )

                public_notify = False
                try:
                    await member.send(explanation)

                except:
                    public_notify = True

                docID = await tools.issue_pun(
                    member.id,
                    self.bot.user.id,
                    'strike',
                    f'[Migrated] {pun["reason"]}',
                    strike_count=strikeCount,
                    context='strike-migration',
                    public=False,
                    public_notify=public_notify,
                )
                db.update_one(
                    {'_id': member.id},
                    {'$set': {'migrate_unnotified': False, 'strike_check': time.time() + (60 * 60 * 24 * 7)}},
                )  # Setting the next expiry check time
                mod = self.bot.get_cog('Moderation Commands')
                await mod.expire_actions(docID, member.guild.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        db = mclient.bowser.puns
        puns = db.find({'user': member.id, 'active': True, 'type': {'$in': ['strike', 'mute', 'blacklist']}})

        mclient.bowser.users.update_one(
            {'_id': member.id},
            {'$push': {'leaves': int(datetime.now(tz=timezone.utc).timestamp())}},
        )
        if puns.count():
            embed = discord.Embed(
                description=f'{member} ({member.id}) left the server\n\n:warning: __**User had active punishments**__ :warning:',
                color=0xD62E44,
                timestamp=datetime.now(tz=timezone.utc),
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
            embed = discord.Embed(color=0x8B572A, timestamp=datetime.now(tz=timezone.utc))

        embed.set_author(name=f'{member} ({member.id})', icon_url=member.display_avatar.url)
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

        embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.now(tz=timezone.utc))
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.display_avatar.url)
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
                if audited.user.id == config.parakarry:
                    return  # Parakarry generates it's own pun records, exit

                reason = audited.reason or '-No reason specified-'
                docID = await tools.issue_pun(audited.target.id, audited.user.id, 'unban', reason, active=False)
                db.update_one({'user': audited.target.id, 'type': 'ban', 'active': True}, {'$set': {'active': False}})

                await tools.send_modlog(
                    self.bot, self.modLogs, 'unban', docID, reason, user=user, moderator=audited.user, public=True
                )

        embed = discord.Embed(color=discord.Color(0x88FF00), timestamp=datetime.now(tz=timezone.utc))
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.display_avatar.url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')

        await self.serverLogs.send(':triangular_flag_on_post: User unbanned', embed=embed)

    @commands.Cog.listener()
    async def on_thread_join(self, thread):
        # on_thread_join is called when a thread is created as well as joined
        if not thread.me:
            # We only want to send an API call if we aren't already in it
            await thread.join()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id:
            return

        if message.type not in [discord.MessageType.default, discord.MessageType.reply]:
            logging.debug(f'on_message discarding non-normal-message: {message.type=}, {message.id=}')
            return

        if not message.guild:
            logging.debug(f'Discarding non guild message {message.channel.type} {message.id}')
            return

        db = mclient.bowser.messages
        timestamp = int(time.time())
        obj = {
            '_id': message.id,
            'author': message.author.id,
            'guild': message.guild.id,
            'channel': message.channel.id,
            'parent_channel': None,
            'content': message.content,
            'timestamp': timestamp,
            'sanitized': False,
        }

        if issubclass(message.channel.__class__, discord.Thread):
            obj['parent_channel'] = message.channel.parent_id

        db.insert_one(obj)

        await self.bot.process_commands(message)  # Allow commands to fire
        return

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):  # TODO: Work with archives channel attribute to list channels
        if not messages[0].guild:
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

        embed = discord.Embed(
            description=f'Archive URL: {config.baseUrl}/logs/{archiveID}',
            color=0xF5A623,
            timestamp=datetime.now(tz=timezone.utc),
        )
        return await self.serverLogs.send(':printer: New message archive generated', embed=embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.cached_message:
            if (
                payload.cached_message.type not in [discord.MessageType.default, discord.MessageType.reply]
                or payload.cached_message.author.bot
            ):
                logging.debug(
                    'on_raw_message_delete discarding non guild message '
                    f'{payload.cached_message.channel.type} {payload.cached_message.id}'
                )
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
            content = (
                '-No saved copy of message content is available-' if not dbMessage['content'] else dbMessage['content']
            )

        embed = discord.Embed(
            description=f'[Jump to message]({jump_url})\n{content}',
            color=0xF8E71C,
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.set_author(name=f'{str(user)} ({user.id})', icon_url=user.display_avatar.url)
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
        if not before.guild:
            logging.debug(f'Discarding non guild edit {before.channel.type} {before.id}')
            return

        if before.content == after.content or before.author.bot:
            return

        if before.type not in [discord.MessageType.default, discord.MessageType.reply]:
            logging.debug(f'on_message_edit discarding non-normal-message: {before.type=}, {before.id=}')
            return  # No system messages

        if not after.content or not before.content:
            return  # Blank or null content (could be embed)

        if len(before.content) <= 1024 and len(after.content) <= 1024:
            embed = discord.Embed(
                description=f'[Jump to message]({before.jump_url})',
                color=0xF8E71C,
                timestamp=datetime.now(tz=timezone.utc),
            )
            embed.add_field(name='Before', value=before.content, inline=False)
            embed.add_field(name='After', value=after.content, inline=False)

        else:
            embed = discord.Embed(
                description=f'[Jump to message]({before.jump_url})\nMessage diff exceeds character limit, view at {config.baseUrl}/logs/{await tools.message_archive([before, after], True)}',
                color=0xF8E71C,
                timestamp=datetime.now(tz=timezone.utc),
            )

        embed.set_author(name=f'{str(before.author)} ({before.author.id})', icon_url=before.author.display_avatar.url)
        embed.add_field(name='Mention', value=f'<@{before.author.id}>')

        await self.serverLogs.send(f':pencil: Message edited in <#{before.channel.id}>', embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        userCol = mclient.bowser.users
        if before.display_name != after.display_name:
            userCol.update_one(
                {'_id': before.id},
                {
                    '$push': {
                        'nameHist': {
                            'str': after.display_name,  # Not escaped. Can be None if nickname removed
                            'type': 'nick',
                            'discriminator': after.discriminator,
                            'timestamp': int(datetime.now(tz=timezone.utc).timestamp()),
                        }
                    }
                },
            )
            if not before.display_name:
                before_name = before.name

            else:
                before_name = discord.utils.escape_markdown(before.display_name)

            if not after.display_name:
                after_name = after.name

            else:
                after_name = discord.utils.escape_markdown(after.display_name)

            embed = discord.Embed(color=0x9535EC, timestamp=datetime.now(tz=timezone.utc))
            embed.set_author(name=f'{before} ({before.id})', icon_url=before.display_avatar.url)
            embed.add_field(name='Before', value=before_name, inline=False)
            embed.add_field(name='After', value=after_name, inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s display name updated', embed=embed)

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
                embed = discord.Embed(color=0x9535EC, timestamp=datetime.now(tz=timezone.utc))
                embed.set_author(name=f'{before} ({before.id})', icon_url=before.display_avatar.url)

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
        if before.name != after.name or before.discriminator != after.discriminator:
            # Only time discrim would be called, and not username (i.e. discrim reroll after name change)
            # is when nitro runs out with a custom discriminator set
            before_name = discord.utils.escape_markdown(str(before))
            after_name = discord.utils.escape_markdown(str(after))
            userCol = mclient.bowser.users

            userCol.update_one(
                {'_id': before.id},
                {
                    '$push': {
                        'nameHist': {
                            'str': after.name,  # We want to keep the integrity without escape characters
                            'type': 'name',
                            'discriminator': after.discriminator,
                            'timestamp': int(datetime.now(tz=timezone.utc).timestamp()),
                        }
                    }
                },
            )
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.now(tz=timezone.utc))
            embed.set_author(name=f'{after} ({after.id})', icon_url=after.display_avatar.url)
            embed.add_field(name='Before', value=before_name, inline=False)
            embed.add_field(name='After', value=after_name, inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s username updated', embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        db = mclient.bowser.users
        for user in db.find({'roles': {'$in': [role.id]}}):
            storedRoles = user['roles']
            storedRoles.remove(role.id)
            db.update_one({'_id': user['_id']}, {'$set': {'roles': storedRoles}})

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class BotUpdateCommand(app_commands.Group):
        pass

    update_group = BotUpdateCommand(name='update', description='Update components of the bot')

    @update_group.command(name='pfp', description='Update the bot profile picture')
    @app_commands.describe(image='The image to use as the new profile picture')
    async def _update_pfp(self, interaction: discord.Interaction, image: discord.Attachment):
        await interaction.response.defer()
        attachment = await image.read()
        await self.bot.user.edit(avatar=attachment)

        return await interaction.followup.send('Done.')

    @update_group.command(name='name', description='Update the bot username')
    @app_commands.describe(name='The new username')
    async def _update_name(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        if len(name) >= 32:
            return await interaction.followup.send(f'{config.redTick} That username is too long.')

        await self.bot.user.edit(username=name)
        return await interaction.followup.send('Done.')

    @update_group.command(
        name='cache', description='Update the database message cache for the entire server. API and resource intensive'
    )
    async def _update_cache(self, interaction: discord.Interaction):
        funcStart = time.time()
        logging.info('[Core] Starting db message sync')
        await interaction.send_message(
            'Starting syncronization of db for all messages in server. This will take a conciderable amount of time.'
        )

        for channel in interaction.guild.channels:
            if not issubclass(channel.__class__, discord.abc.Messageable):
                continue

            # Because this will definitely exceed the interaction expiry, send messages to the channel directly
            await interaction.channel.send(f'Starting syncronization for <#{channel.id}>')

            try:
                x, y = await self.store_message_cache(channel)
                await interaction.channel.send(
                    f'Syncronized <#{channel.id}>. Processed {x} messages and recorded meta data for {y} messages'
                )

            except (discord.Forbidden, discord.HTTPException):
                await interaction.channel.send(f'Failed to syncronize <#{channel.id}>')

        timeToComplete = tools.humanize_duration(tools.resolve_duration(f'{int(time.time() - funcStart)}s'))
        return await interaction.channel.send(
            f'<@{interaction.user.id}> Syncronization completed. Took {timeToComplete}'
        )

    @app_commands.command(name='shutdown', description='Shutdown the bot and all modules')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(manage_guild=True)
    async def _shutdown(self, interaction: discord.Interaction):
        await interaction.response.send_message('Closing connection to discord and shutting down')
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

        return x, y


async def setup(bot):
    await bot.add_cog(MainEvents(bot))
    logging.info('[Extension] Main module loaded')


async def teardown(bot):
    await bot.remove_cog('MainEvents')
    logging.info('[Extension] Main module unloaded')
