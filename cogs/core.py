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
        self.bot.load_extension('cogs.moderation')
        self.bot.load_extension('cogs.utility')
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.debugChannel = self.bot.get_channel(config.debugChannel)

    #@commands.Cog.listener()
    #async def on_ready(self):
    #    self.serverLogs = self.bot.get_channel(config.logChannel)
    #    self.modLogs = self.bot.get_channel(config.modChannel)
    #    self.debugChannel = self.bot.get_channel(config.debugChannel)

    @commands.Cog.listener()
    async def on_resume(self):
        logging.warning('[MAIN] The bot has been resumed on Discord')

    @commands.Cog.listener()
    async def on_member_join(self, member):
        db = mclient.fil.users
        doc = db.find_one({'_id': member.id})
        roleList = []

        if not doc:
            restored = False
            await utils.store_user(member)

        else:
            if doc['roles']:
                restored = True
                for x in doc['roles']:
                    role = member.guild.get_role(x)
                    if role:
                        roleList.append(role)
    
                await member.edit(roles=roleList, reason='Automatic role restore action')

            else:
                restored = False
        
        joinEmbed = discord.Embed(color=discord.Color(0x4f941e), description=f'User <@{member.id}> joined.', timestamp=datetime.datetime.utcnow())
        joinEmbed.set_author(name=f'User joined | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
        await self.serverLogs.send(embed=joinEmbed)

        if restored:
            roleText = ''
            for z in roleList:
                roleText += f'{z}, '

            restoreEmbed = discord.Embed(color=discord.Color(0x25a5ef), description=f'Returning member <@{member.id}> has been restored', timestamp=datetime.datetime.utcnow())
            restoreEmbed.set_author(name=f'User restored | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
            restoreEmbed.add_field(name='Restored roles', value=roleText[:-2])
            await self.serverLogs.send(embed=restoreEmbed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        embed = discord.Embed(color=discord.Color(0x772F30), description=f'User <@{member.id}> left.', timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'User left | {member.name}#{member.discriminator}', icon_url=member.avatar_url)
        await self.serverLogs.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
    
        if message.channel.type != discord.ChannelType.text:
            logging.error(f'Discarding bad message {message.channel.type}')
            return

        db = mclient.fil.users
        doc = db.find_one_and_update({'_id': message.author.id}, {'$inc': {'messages': 1}, '$set': {'last_message': int(time.time())}})
        if not doc:
            await utils.store_user(message.author, 1)

        return await self.bot.process_commands(message) # Allow commands to fire

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        db = mclient.fil.archive
        oneDayPast = int(time.time() - 30)
        archives = db.find({'timestamp': {'$gt': oneDayPast}})
        if archives: # If the bulk delete is the result of us, exit
            for x in archives:
                if messages[0].id in x[messages]:
                    return

        archiveID = await utils.message_archive(messages)

        embed = discord.Embed(color=discord.Color(0xff6661), description=f'A bulk delete has occured, view message logs at {config.baseUrl}/archive/{archiveID}', timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Messages deleted | Bulk Delete')
        await self.bot.get_channel(config.logChannel).send(embed=embed)
        return await self.serverLogs.send()

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.type != discord.MessageType.default or message.author.bot:
            return # No system messages

        if not message.content:
            return # Blank or null content (could be embed)

        # Discord allows 1024 chars per embed field value, but a message can have 2000 chars
        content = message.content if len(message.content) <= 1024 else f'Message exceeds character limit, view at {config.baseUrl}/archive/{await utils.message_archive(message)}'

        embed = discord.Embed(color=discord.Color(0xff6661), description=f'Message by <@{message.author.id}> in <#{message.channel.id}> was deleted.', timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Message deleted | {message.author.name}#{message.author.discriminator}')
        embed.add_field(name='Message', value=content)
        await self.serverLogs.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content == after.content:
            return

        if before.type != discord.MessageType.default:
            return # No system messages

        if not after.content or not before.content:
            return # Blank or null content (could be embed)

        # Discord allows 1024 chars per embed field value, but a message can have 2000 chars
        before_content = before.content if len(before.content) < 1000 else before.content[:1000] + '...'
        after_content = after.content if len(after.content) < 1000 else after.content[:1000] + '...'
    
        embed = discord.Embed(color=discord.Color(0x25a5ef), description=f'Message by <@{before.author.id}> in <#{before.channel.id}> was edited.', timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Message edited | {before.author.name}#{before.author.discriminator}')
        embed.add_field(name='Before', value=before_content, inline=True)
        embed.add_field(name='After', value=after_content, inline=True)
        await self.serverLogs.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def update(self, ctx, sub, *args):
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

def setup(bot):
    bot.add_cog(MainEvents(bot))
    logging.info('[Extension] Main module loaded')

def teardown(bot):
    bot.remove_cog('MainEvents')
    logging.info('[Extension] Main module unloaded')
