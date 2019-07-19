import asyncio
import datetime
import logging
import time

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
            self.bot.load_extension('utils')
        except discord.ext.commands.errors.ExtensionAlreadyLoaded:
            pass

        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.debugChannel = self.bot.get_channel(config.debugChannel)

    @commands.Cog.listener()
    async def on_resume(self):
        logging.warning('[MAIN] The bot has been resumed on Discord')

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

        new = ':new:' if (datetime.datetime.utcnow() - member.created_at).total_seconds() <= 60 * 60 * 24 * 14 else '' # Two weeks

        log = f':inbox_tray: {new} User **{str(member)}** ({member.id}) joined'
        await self.serverLogs.send(log)

        if restored:
            roleText = ', '.split(x.name for x in roleList)

            logRestore = f':shield: Roles have been restored for returning member **{str(member)}** ({member.id}):\n{roleText}'
            await self.serverLogs.send(logRestore)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        log = f':outbox_tray: User **{str(member)}** ({member.id}) left'
        await self.serverLogs.send(log)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id:
            return
    
        if message.channel.type != discord.ChannelType.text:
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
        db = mclient.bowser.archive
        checkStamp = int(time.time() - 600) # Rate limiting, instability, and being just slow to fire are other factors that could delay the event
        archives = db.find({'timestamp': {'$gt': checkStamp}})
        print(archives)
        if archives: # If the bulk delete is the result of us, exit
            for x in archives:
                if messages[0].id in x['messages']:
                    return

        archiveID = await utils.message_archive(messages)

        log = f':printer: New message archive has been generated, view it at {config.baseUrl}/archive/{archiveID}'
        return await self.serverLogs.send(log)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.type != discord.MessageType.default or message.author.bot:
            return # No system messages

        if not message.content:
            return # Blank or null content (could be embed)

        log = f':wastebasket: Message by **{str(message.author)}** ({message.author.id}) in <#{message.channel.id}> deleted:\n'
        content = message.content if (len(log) + len(message.clean_content)) < 2000 else 'Message exceeds character limit, ' \
            f'view at {config.baseUrl}/archive/{await utils.message_archive(message)}'
        log += content
        
        await self.serverLogs.send(log)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content == after.content:
            return

        if before.type != discord.MessageType.default:
            return # No system messages

        if not after.content or not before.content:
            return # Blank or null content (could be embed)

        log = f':pencil: Message by **{str(before.author)}** ({before.author.id}) in <#{before.channel.id}> edited:\n'
        editedMsg = f'__Before:__ {before.clean_content}\n\n__After:__ {after.clean_content}'
        fullLog = log + editedMsg if (len(log) + len(editedMsg)) < 2000 else log + 'Message exceeds character limit, ' \
            f'view at {config.baseUrl}/archive/{await utils.message_archive([before, after], True)}'
        

        await self.serverLogs.send(fullLog)

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

        else:
            return await ctx.send('Invalid sub command')

    @commands.command(name='shutdown')
    @commands.is_owner()
    async def _shutdown(self, ctx):
        await ctx.send('Closing connection to discord and shutting down')
        return await self.bot.close()

def setup(bot):
    bot.add_cog(MainEvents(bot))
    logging.info('[Extension] Main module loaded')

def teardown(bot):
    bot.remove_cog('MainEvents')
    logging.info('[Extension] Main module unloaded')
