import asyncio
import logging
import re
import typing
import datetime

import pymongo
import discord
from discord.ext import commands

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

serverLogs = None
modLogs = None
SMM2LevelID = re.compile(r'([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})', re.I | re.M)
SMM2LevelPost = re.compile(r'Name: ?(.+)\n\n?(?:Level )?ID: ?([0-9a-z]{3}-[0-9a-z]{3}-[0-9a-z]{3})(?:\s+)?\n\n?Style: ?(.+)\n\n?(?:Theme: ?(.+)\n\n?)?(?:Tags: ?(.+)\n\n?)?Difficulty: ?(.+)\n\n?Description: ?(.+)', re.I)
SMM2LevelPost = re.compile(r'Name: ?(\S.*)\n\n?(?:Level )?ID:\s*((?:[0-9a-z]{3}-){2}[0-9a-z]{3})(?:\s+)?\n\n?Style: ?(\S.*)\n\n?(?:Theme: ?(\S.*)\n\n?)?(?:Tags: ?(\S.*)\n\n?)?Difficulty: ?(\S.*)\n\n?Description: ?(\S.*)', re.I)

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
        await self.bot.wait_until_ready()
        if message.author.bot or message.type != discord.MessageType.default:
            # This a trash message, we don't want to track this
            return

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

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.bot.wait_until_ready()

        if message.author.bot or message.type != discord.MessageType.default:
            return

        #Filter for #mario
        if message.channel.id == 325430144993067049: # #mario
            if re.search(SMM2LevelID, message.content):
                await message.delete()
                response = await message.channel.send(f'<:redTick:402505117733224448> <@{message.author.id}> Please do not post Super Mario Maker 2 level codes '\
                    'here. Post in <#595203237108252672> with the pinned template instead.')

                await response.delete(delay=20)
            return

        #Filter for #smm2-levels
        if message.channel.id == 595203237108252672:
            if not re.search(SMM2LevelID, message.content):
                # We only want to filter posts with a level id
                return

            block = re.search(SMM2LevelPost, message.content)
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

@commands.command(name='info')
@commands.has_any_role(config.moderator, config.eh)
async def _info(ctx, user: typing.Union[discord.Member, int]):
    if type(user) == int:
        # User shares no servers, fetch it instead
        user = await Client.fetch_user(user)
        if not user:
            return await ctx.send('<:redTick:402505117733224448> User does not exist')
        
        embed = discord.Embed(color=discord.Color(0x18EE1C), description=f'Fetched information about this user (<@{user.id}>) from the ' \
        'API as I share no servers. There is little information to display as such')
        embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
        embed.set_thumbnail(url=user.avatar_url)
        embed.add_field(name='Created', value=f'{user.created_at}T UTC')
        return await ctx.send(embed=embed) # TODO: Return DB info if it exists as well

    else:
        # Member object, loads of info to work with
        db = mclient.fil.users
        doc = db.find_one({'_id': user.id})
        embed = discord.Embed(color=discord.Color(0x18EE1C), description=f'Fetched member <@{user.id}>')
        embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.avatar_url)
        embed.set_thumbnail(url=user.avatar_url)
        embed.add_field(name='Messages', value=str(doc['messages']), inline=True)
        embed.add_field(name='Join date', value=user.joined_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)
        roles = ''
        for role in user.roles:
            if role.id == user.guild.id:
                continue

            roles += f'{role.name}, '
            
        if not roles:
            # Empty, or no roles
            roles = '*User has no roles*'

        embed.add_field(name='Roles', value=roles[:-2], inline=False)
        if doc['last_message'] == None:
            lastMsg = 'N/a'

        lastMsg = 'N/a' if not doc['last_message'] else datetime.datetime.utcfromtimestamp(doc['last_message']).strftime('%B %d, %Y %H:%M:%S UTC')
        embed.add_field(name='Last message', value=lastMsg, inline=True)
        embed.add_field(name='Created', value=user.created_at.strftime('%B %d, %Y %H:%M:%S UTC'), inline=True)
        punishments = ''
        if not doc['punishments']:
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
            for pun in doc['punishments']:
                if puns > 5:
                    break

                puns += 1
                stamp = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%M/%d/%y %H:%M:%S UTC')
                punType = punStrs[pun['type']]
                if pun['type'] in ['clear', 'unmute', 'unban']:
                    punishments += f'- [{stamp}] {punType}\n'

                else:
                    punishments += f'+ [{stamp}] {punType}\n'

            punishments = f'Showing {puns}/{len(doc["punishments"])} punishment entries. ' \
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

    bot.add_cog(ChatFilter(bot))
    bot.add_command(_info)

    logging.info('Utility module loaded')

def teardown(bot):
    bot.remove_cog('Chat Filter')
    logging.info('Utility module unloaded')