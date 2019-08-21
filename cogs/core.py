import asyncio
import datetime
import logging
import time
import typing

import pymongo
import discord
from discord.ext import commands

import config
import utils

startTime = int(time.time())
mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class MainEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            self.bot.load_extension('cogs.moderation')
            self.bot.load_extension('cogs.utility')
            #self.bot.load_extension('cogs.filter')
            self.bot.load_extension('utils')
        except discord.ext.commands.errors.ExtensionAlreadyLoaded:
            pass

        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.debugChannel = self.bot.get_channel(config.debugChannel)

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
            await utils.store_user(member)

        else:
            if doc['roles']:
                for x in doc['roles']:
                    if x == member.guild.id:
                        continue

                    restored = True
                    role = member.guild.get_role(x)
                    if role:
                        roleList.append(role)
    
                await member.edit(roles=roleList, reason='Automatic role restore action')

        new = ':new: ' if (datetime.datetime.utcnow() - member.created_at).total_seconds() <= 60 * 60 * 24 * 14 else '' # Two weeks

        #log = f':inbox_tray: {new} User **{str(member)}** ({member.id}) joined'

        embed = discord.Embed(color=0x417505, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar_url)
        created_at = member.created_at.strftime(f'{new}%B %d, %Y %H:%M:%S UTC')
        created_at += '' if not new else ' (account created less than 14 days ago)'
        embed.add_field(name='Created at', value=created_at)
        embed.add_field(name='Mention', value=f'<@{member.id}>')

        await self.serverLogs.send(':inbox_tray: User joined', embed=embed)

        if restored:
            #roleText = ', '.split(x.name for x in roleList)

            #logRestore = f':shield: Roles have been restored for returning member **{str(member)}** ({member.id}):\n{roleText}'
            punTypes = {
                'mute': 'Mute',
                'blacklist': 'Channel Blacklist ({})',
                'tier1': 'Tier 1 Warning',
                'tier2': 'Tier 2 Warning',
                'tier3': 'Tier 3 Warning',
            }
            puns = mclient.bowser.puns.find({'user': member.id, 'active': True})
            restoredPuns = []
            if puns.count():
                for x in puns:
                    if x['type'] == 'blacklist':
                        restoredPuns.append(punTypes[x['type']].format(x['context']))

                    else:
                        restoredPuns.append(punTypes[x['type']])

            embed = discord.Embed(color=0x4A90E2, timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar_url)
            embed.add_field(name='Restored roles', value=', '.join(x.name for x in roleList))
            if restoredPuns:
                embed.add_field(name='Restored punishments', value=', '.join(restoredPuns))
            embed.add_field(name='Mention', value=f'<@{member.id}>')
            await self.serverLogs.send(':shield: Member restored', embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        #log = f':outbox_tray: User **{str(member)}** ({member.id}) left'
        db = mclient.bowser.puns
        puns = db.find({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3',
                        'mute',
                        'blacklist'
                    ]
                }
            }
        )
        if puns.count():
            embed = discord.Embed(description=f'{member} ({member.id}) left the server\n\n:warning: __**User had active punishments**__ :warning:', color=0xD62E44, timestamp=datetime.datetime.utcnow())
            punishments = []
            for x in puns:
                punishments.append(config.punStrs[x['type']])

            punishments = ', '.join(punishments)
            embed.add_field(name='Punishment types', value=punishments)

        else:
            embed = discord.Embed(color=0x8B572A, timestamp=datetime.datetime.utcnow())

        embed.set_author(name=f'{member} ({member.id})', icon_url=member.avatar_url)
        embed.add_field(name='Mention', value=f'<@{member.id}>')
        await self.serverLogs.send(':outbox_tray: User left', embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user): # TODO: make all guild ID mentions to config instead
        if guild.id != 238080556708003851:
            return

        db = mclient.bowser.puns
        if not db.find_one({'user': user.id, 'type': 'ban', 'active': True, 'timestamp': {'$gt': time.time() - 60}}):
            # Manual ban
            audited = False
            async for entry in guild.audit_logs(action=discord.AuditLogAction.ban):
                if entry.target == user:
                    audited = entry
                    break

            if audited:
                reason = '-No reason specified-' if not audited.reason else audited.reason
                await utils.issue_pun(audited.target.id, audited.user.id, 'ban', reason)

                embed = discord.Embed(description='User was manually banned through Discord', color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Ban | {audited.target}')
                embed.add_field(name='User', value=audited.target.mention, inline=True)
                embed.add_field(name='Moderator', value=audited.user.mention, inline=True)
                embed.add_field(name='Reason', value=reason)

                await self.modLogs.send(embed=embed)

        embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.avatar_url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')

        await self.serverLogs.send(':rotating_light: User banned', embed=embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        if guild.id != 238080556708003851:
            return

        db = mclient.bowser.puns
        if not db.find_one({'user': user.id, 'type': 'unban', 'timestamp': {'$gt': time.time() - 60}}):
            # Manual unban
            audited = False
            async for entry in guild.audit_logs(action=discord.AuditLogAction.unban):
                if entry.target == user:
                    audited = entry
                    break

            if audited:
                reason = '-No reason specified-' if not audited.reason else audited.reason
                await utils.issue_pun(audited.target.id, audited.user.id, 'unban', reason, active=False)
                db.update_one({'user': audited.target.id, 'type': 'ban', 'active': True}, {'$set':{
                    'active': False
                }})

                embed = discord.Embed(description='User was manually unbanned through Discord', color=discord.Color(0x4A90E2), timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Unban | {audited.target}')
                embed.add_field(name='User', value=audited.target.mention, inline=True)
                embed.add_field(name='Moderator', value=audited.user.mention, inline=True)
                embed.add_field(name='Reason', value=reason)

                await self.modLogs.send(embed=embed)

        embed = discord.Embed(color=discord.Color(0x88FF00), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{user} ({user.id})', icon_url=user.avatar_url)
        embed.add_field(name='Mention', value=f'<@{user.id}>')

        await self.serverLogs.send(':triangular_flag_on_post: User unbanned', embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id:
            return
    
        if message.channel.type not in [discord.ChannelType.text, discord.ChannelType.news]:
            logging.error(f'Discarding bad message {message.channel.type}')
            return

        db = mclient.bowser.messages
        db.insert_one({
            '_id': message.id,
            'author': message.author.id,
            'guild': message.guild.id,
            'channel': message.channel.id,
            'timestamp': int(time.time())
        })

        return await self.bot.process_commands(message) # Allow commands to fire

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages): # TODO: Work with archives channel attribute to list channels
        await asyncio.sleep(10) # Give chance for clean command to finish and discord to process delete
        db = mclient.bowser.archive
        checkStamp = int(time.time() - 600) # Rate limiting, instability, and being just slow to fire are other factors that could delay the event
        archives = db.find({'timestamp': {'$gt': checkStamp}})
        if archives: # If the bulk delete is the result of us, exit
            for x in archives:
                if messages[0].id in x['messages']:
                    return

        archiveID = await utils.message_archive(messages)

        #log = f':printer: New message archive has been generated, view it at {config.baseUrl}/archive/{archiveID}'
        embed = discord.Embed(description=f'Archive URL: {config.baseUrl}/archive/{archiveID}', color=0xF5A623, timestamp=datetime.datetime.utcnow())
        return await self.serverLogs.send(':printer: New message archive generated', embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.type != discord.MessageType.default or message.author.bot:
            return # No system messages

        if not message.content:
            return # Blank or null content (could be embed)

        #log = f':wastebasket: Message by **{str(message.author)}** ({message.author.id}) in <#{message.channel.id}> deleted:\n'
        #content = message.content if (len(log) + len(message.clean_content)) < 2000 else 'Message exceeds character limit, ' \
        #    f'view at {config.baseUrl}/archive/{await utils.message_archive(message)}'
        #log += content

        embed = discord.Embed(description=f'**Content:**\n\n{message.content}', color=0xF8E71C, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{str(message.author)} ({message.author.id})', icon_url=message.author.avatar_url)
        embed.add_field(name='Mention', value=f'<@{message.author.id}>')
        
        await self.serverLogs.send(f':wastebasket: Message deleted in <#{message.channel.id}>', embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content == after.content:
            return

        if before.type != discord.MessageType.default:
            return # No system messages

        if not after.content or not before.content:
            return # Blank or null content (could be embed)

        #log = f':pencil: Message by **{str(before.author)}** ({before.author.id}) in <#{before.channel.id}> edited:\n'
        #editedMsg = f'__Before:__ {before.clean_content}\n\n__After:__ {after.clean_content}'
        #fullLog = log + editedMsg if (len(log) + len(editedMsg)) < 2000 else log + 'Message exceeds character limit, ' \
        #    f'view at {config.baseUrl}/archive/{await utils.message_archive([before, after], True)}'

        if len(before.content) <= 1024 and len(after.content) <= 1024:
            embed = discord.Embed(color=0xF8E71C, timestamp=datetime.datetime.utcnow())
            embed.add_field(name='Before', value=before.content, inline=False)
            embed.add_field(name='After', value=after.content, inline=False)

        else:
            embed = discord.Embed(description=f'Message diff exceeds character limit, view at {config.baseUrl}/archive/{await utils.message_archive([before, after], True)}', color=0xF8E71C, timestamp=datetime.datetime.utcnow())
        
        embed.set_author(name=f'{str(before.author)} ({before.author.id})', icon_url=before.author.avatar_url)
        embed.add_field(name='Mention', value=f'<@{before.author.id}>')

        await self.serverLogs.send(f':pencil: Message edited in <#{before.channel.id}>', embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        userCol = mclient.bowser.users
        if before.nick != after.nick:
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'{str(before)} ({before.id})', icon_url=before.avatar_url)
            embed.add_field(name='Before', value=before.name if not before.nick else before.nick, inline=False)
            embed.add_field(name='After', value=after.nick, inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s nickname updated', embed=embed)

        if before.roles != after.roles:
            roleList = []
            roleStr = []
            oldRoleStr = []
            for x in after.roles:
                if x.id == before.guild.id:
                    continue

                roleList.append(x.id)
                roleStr.append(x.name)

            for n in before.roles:
                if n.id == before.guild.id:
                    continue

                oldRoleStr.append(n.name)

            roleStr = ['*No roles*'] if not roleStr else roleStr
            oldRoleStr = ['*No roles*'] if not oldRoleStr else oldRoleStr

            userCol.update_one({'_id': before.id}, {'$set': {'roles': roleList}})

            embed = discord.Embed(color=0x9535EC, timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'{str(before)} ({before.id})', icon_url=before.avatar_url)
            embed.add_field(name='Before', value=', '.join(n for n in reversed(oldRoleStr)), inline=False)
            embed.add_field(name='After', value=', '.join(n for n in reversed(roleStr)), inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':closed_lock_with_key: User\'s roles updated', embed=embed)

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        if before.name != after.name:
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'{str(after)} ({after.id})', icon_url=after.avatar_url)
            embed.add_field(name='Before', value=str(before), inline=False)
            embed.add_field(name='After', value=str(after), inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s name updated', embed=embed)

        elif before.discriminator != after.discriminator:
            # Really only case this would be called, and not username (i.e. discrim reroll after name change)
            # is when nitro runs out with a custom discriminator set
            embed = discord.Embed(color=0x9535EC, timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'{str(after)} ({after.id})', icon_url=after.avatar_url)
            embed.add_field(name='Before', value=str(before), inline=False)
            embed.add_field(name='After', value=str(after), inline=False)
            embed.add_field(name='Mention', value=f'<@{before.id}>')

            await self.serverLogs.send(':label: User\'s name updated', embed=embed)

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
            await ctx.send('Starting syncronization of db for all messages in server. This will take a conciderable amount of time.')
            for channel in ctx.guild.channels:
                if channel.type != discord.ChannelType.text:
                    continue

                await ctx.send(f'Starting syncronization for <#{channel.id}>')

                try:
                    x, y = await self.store_message_cache(channel)
                    await ctx.send(f'Syncronized <#{channel.id}>. Processed {x} messages and recorded meta data for {y} messages')

                except (discord.Forbidden, discord.HTTPException):
                    await ctx.send(f'Failed to syncronize <#{channel.id}>')

            timeToComplete = utils.humanize_duration(utils.resolve_duration(f'{int(time.time() - funcStart)}s'))
            return await ctx.send(f'<@{ctx.author.id}> Syncronization completed. Took {timeToComplete}')

        else:
            return await ctx.send('Invalid sub command')

    @commands.command(name='pundb')
    @commands.is_owner()
    async def _pundb(self, ctx, _type, user, moderator, strTime, active: typing.Optional[bool], *, reason='-No reason specified-'):
        date = datetime.datetime.strptime(strTime, '%m/%d/%y')
        expiry = None if not active else int(date.timestamp() + (60 * 60 * 24 * 30))
        await utils.issue_pun(int(user), int(moderator), _type, reason, expiry, active, 'old', date.timestamp())
        await ctx.send(f'{config.greenTick} Done')

    @commands.command(name='shutdown')
    @commands.is_owner()
    async def _shutdown(self, ctx):
        await ctx.send('Closing connection to discord and shutting down')
        return await self.bot.close()

    async def store_message_cache(self, channel):
        #users = mclient.bowser.users
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
                db.insert_one({
                    '_id': message.id,
                    'author': message.author.id,
                    'guild': message.guild.id,
                    'channel': message.channel.id,
                    'timestamp': int(message.created_at.timestamp())
                })
                #if not users.find_one({'_id': message.author.id}):
                    #users.insert_one({'_id': message.author.id, 'roles': []})

            #else:
                #if not users.find_one({'_id': message.author.id}):
                    #users.insert_one({'_id': message.author.id, 'roles': []})

        return x, y

def setup(bot):
    bot.add_cog(MainEvents(bot))
    logging.info('[Extension] Main module loaded')

def teardown(bot):
    bot.remove_cog('MainEvents')
    logging.info('[Extension] Main module unloaded')
