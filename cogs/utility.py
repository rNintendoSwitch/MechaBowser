import asyncio
import logging
import re
import typing
import datetime
import aiohttp

import pymongo
import discord
from discord import Webhook, AsyncWebhookAdapter
from discord.ext import commands, tasks
import pymarkovchain

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

serverLogs = None
modLogs = None

class MarkovChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.markovChain = pymarkovchain.MarkovChain('markov')
        #self.dump_markov.start() # pylint: disable=no-member

    def cog_unload(self):
        pass
        #self.dump_markov.cancel() # pylint: disable=no-member

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.content or message.content.startswith('()'):
            # Might be an embed or a command, not useful
            return

        self.markovChain.generateDatabase(message.content)
        self.markovChain.dumpdb()

    @tasks.loop(seconds=30)
    async def dump_markov(self):
        logging.info('Taking a dump')
        self.markovChain.dumpdb()
        logging.info('I\'m done')

    @commands.command(name='markov')
    @commands.is_owner()
    async def _markov(self, ctx, seed: typing.Optional[str]):
        try:
            if seed:
                return await ctx.send(self.markovChain.generateStringWithSeed(seed))

            else:
                return await ctx.send(self.markovChain.generateString())
        except pymarkovchain.StringContinuationImpossibleError:
            return await ctx.send(':warning: Unable to generate chain with provided seed')

class ChatControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.SMM2LevelID = re.compile(r'([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})', re.I | re.M)
        self.SMM2LevelPost = re.compile(r'Name: ?(\S.*)\n\n?(?:Level )?ID:\s*((?:[0-9a-z]{3}-){2}[0-9a-z]{3})(?:\s+)?\n\n?Style: ?(\S.*)\n\n?(?:Theme: ?(\S.*)\n\n?)?(?:Tags: ?(\S.*)\n\n?)?Difficulty: ?(\S.*)\n\n?Description: ?(\S.*)', re.I)
        self.affiliateLinks = re.compile(r'(https?:\/\/(?:.*\.)?(?:(?:amazon)|(?:bhphotovideo)|(?:bestbuy)|(?:ebay)|(?:gamestop)|(?:groupon)|(?:newegg(?:business)?)|(?:stacksocial)|(?:target)|(?:tigerdirect)|(?:walmart))\.[a-z\.]{2,7}\/.*)(?:\?.+)', re.I)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.type != discord.MessageType.default:
            return

        #Filter test for afiliate links
        if message.channel.id in [314857672585248768, 276036563866091521]:
            if re.search(self.affiliateLinks, message.content):
                hooks = await message.channel.webhooks()
                useHook = await message.channel.create_webhook(name=f'mab_{message.channel.id}', reason='No webhooks existed; 1<= required for chat filtering') if not hooks else hooks[0]

                await message.delete()
                async with aiohttp.ClientSession() as session:
                    name = message.author.name if not message.author.nick else message.author.nick
                    webhook = Webhook.from_url(useHook.url, adapter=AsyncWebhookAdapter(session))
                    await webhook.send(content=re.sub(self.affiliateLinks, r'\1', message.content), username=name, avatar_url=message.author.avatar_url)

        #Filter for #mario
        if message.channel.id == 325430144993067049: # #mario
            if re.search(self.SMM2LevelID, message.content):
                await message.delete()
                response = await message.channel.send(f'<:redTick:402505117733224448> <@{message.author.id}> Please do not post Super Mario Maker 2 level codes '\
                    'here. Post in <#595203237108252672> with the pinned template instead.')

                await response.delete(delay=20)
            return

        #Filter for #smm2-levels
        if message.channel.id == 595203237108252672:
            if not re.search(self.SMM2LevelID, message.content):
                # We only want to filter posts with a level id
                return

            block = re.search(self.SMM2LevelPost, message.content)
            if not block:
                # No match for a properly formatted level post
                response = await message.channel.send(f'<:redTick:402505117733224448> <@{message.author.id}> Your level is formatted incorrectly, please see the pinned messages for the format. A copy '\
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

    @commands.command(name='ping')
    async def _ping(self, ctx):
        initiated = ctx.message.created_at
        msg = await ctx.send('Evaluating...')
        return await msg.edit(content=f'Pong! Roundtrip latency {(msg.created_at - initiated).total_seconds()} seconds')

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
                reaction = await Client.wait_for('reaction_add', timeout=15, check=confirm_check)
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

        deleted = await ctx.channel.purge(limit=messages, check=message_filter, bulk=True)
    
        m = await ctx.send('Clean action complete')
        await m.delete(delay=10)
        archiveID = await utils.message_archive(deleted)

        embed = discord.Embed(color=discord.Color(0xff6661), description=f'A bulk delete has occured, you can view these messags at {config.baseUrl}/archive/{archiveID}', timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Messages deleted | Bulk delete')
        await Client.get_channel(config.logChannel).send(embed=embed)

        return await m.delete(delay=10)

    @commands.command(name='info')
    @commands.has_any_role(config.moderator, config.eh)
    async def _info(self, ctx, user: typing.Union[discord.Member, int]):
        if type(user) == int:
            # User doesn't share the ctx server, fetch it instead
            try:
                user = await Client.fetch_user(user)

            except discord.NotFound:
                return await ctx.send('<:redTick:402505117733224448> User does not exist')
        
            embed = discord.Embed(color=discord.Color(0x18EE1C), description=f'Fetched information about this user (<@{user.id}>) from the ' \
            'API as I do not share this server with them. There may little information to display as such')
            embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
            embed.set_thumbnail(url=user.avatar_url)
            embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'))
            return await ctx.send(embed=embed) # TODO: Return DB info if it exists as well

        else:
            # Member object, loads of info to work with
            messages = mclient.bowser.messages.find({'author': user.id})
            msgCount = 0 if not messages else messages.count()

            embed = discord.Embed(color=discord.Color(0x18EE1C), description=f'Fetched member <@{user.id}>')
            embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
            embed.set_thumbnail(url=user.avatar_url)
            embed.add_field(name='Messages', value=str(msgCount), inline=True)
            embed.add_field(name='Join date', value=user.joined_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)
            roleList = []
            for role in reversed(user.roles):
                if role.id == user.guild.id:
                    continue

                roleList.append(role.name)
            
            if not roleList:
                # Empty; no roles
                roles = '*User has no roles*'

            else:
                roles = ', '.join(roleList)

            embed.add_field(name='Roles', value=roles, inline=False)

            lastMsg = 'N/a' if msgCount == 0 else datetime.datetime.utcfromtimestamp(messages.sort('timestamp', 1)[0]['timestamp']).strftime('%B %d, %Y %H:%M:%S UTC')
            embed.add_field(name='Last message', value=lastMsg, inline=True)
            embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)
            punishments = ''
            punsCol = mclient.bowser.puns.find({'user': user.id})
            if not punsCol:
                punishments = '__*No punishments on record*__'

            else:
                punStrs = {
                    'tier1': 'Tier 1 Warning',
                    'tier2': 'Tier 2 Warning',
                    'tier3': 'Tier 3 Warning',
                    'mute': 'Mute',
                    'unmute': 'Unmute',
                    'clear': 'Warnings reset',
                    'kick': 'Kick',
                    'ban': 'Ban',
                    'unban': 'Unban'
                }
                puns = 0
                for pun in punsCol:
                    if puns > 5:
                        break

                    puns += 1
                    stamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%m/%d/%y %H:%M:%S UTC')
                    punType = punStrs[pun['type']]
                    if pun['type'] in ['clear', 'unmute', 'unban']:
                        punishments += f'- [{stamp}] {punType}\n'

                    else:
                        punishments += f'+ [{stamp}] {punType}\n'

                punishments = f'Showing {puns}/{punsCol.count()} punishment entries. ' \
                    f'For a full history including responsible moderator, active status, and more use `{ctx.prefix}history @{str(user)}` or `{ctx.prefix}history {user.id}`' \
                    f'\n```diff\n{punishments}```'
        embed.add_field(name='Punishments', value=punishments)
        return await ctx.send(embed=embed)

def setup(bot):
    global serverLogs
    global modLogs
    global Client

    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)
    Client = bot

    bot.add_cog(ChatControl(bot))
    #bot.add_cog(MarkovChat(bot))
    logging.info('[Extension] Utility module loaded')

def teardown(bot):
    bot.remove_cog('ChatControl')
    #bot.remove_cog('MarkovChat')
    logging.info('[Extension] Utility module unloaded')