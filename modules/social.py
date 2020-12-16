import asyncio
import typing
import io
import logging
import re
import datetime
import pytz
import time
from pathlib import Path

import numpy as np
import pymongo
import gridfs
import requests
import codepoints
import discord
from discord.ext import commands
from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw
from emoji import UNICODE_EMOJI
from fuzzywuzzy import process

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class SocialFeatures(commands.Cog, name='Social Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.inprogressEdits = {}
        self.letterCodepoints = ['1f1e6', '1f1e7', '1f1e8', '1f1e9', '1f1ea', '1f1eb', '1f1ec', '1f1ed', '1f1ee', '1f1ef', '1f1f0', '1f1f1', '1f1f2', '1f1f3', '1f1f4', '1f1f5', '1f1f6', '1f1f7', '1f1f8', '1f1f9', '1f1fa', '1f1fb', '1f1fc', '1f1fd', '1f1fe', '1f1ff']
        self.twemojiPath = 'resources/twemoji/assets/72x72/'
        
        self.friendCodeRegex = { # Friend Code Regexs (\u2014 = em-dash)
            # Profile setup/editor (lenient)
            "profile": re.compile(r'(?:sw)?[ \-\u2014]?(\d{4})[ \-\u2014]?(\d{4})[ \-\u2014]?(\d{4})', re.I),
            # Chat filter, "It appears you've sent a friend code." Requires separators and discards select prefixes.
            # Discarded prefixes: MA/MO (AC Designer), DA (AC Dream Address).
            "chatFilter": re.compile(r'(sw|m[^ao]|d[^a]|[^MD]\w|^\w|^)[ \-\u2014]?\d{4}[ \-\u2014]\d{4}[ \-\u2014]\d{4}', re.I + re.M)
        }

    @commands.group(name='profile', invoke_without_command=True)
    @commands.cooldown(2, 60, commands.BucketType.channel)
    async def _profile(self, ctx, member: typing.Optional[discord.Member]):
        if not member: member = ctx.author
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})

        # If profile not setup, running on self, not a mod, and not in commands channel: disallow running profile command
        if not dbUser['profileSetup'] and member == ctx.author and ctx.guild.get_role(config.moderator) not in ctx.author.roles and ctx.channel.id != config.commandsChannel: 
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You need to setup your profile to view it outside of <#{config.commandsChannel}>! To setup your profile, use `!profile edit` in <#{config.commandsChannel}>.', delete_after=15)

        card = await self._generate_profile_card(member)
        await ctx.send(file=card)

    async def _generate_profile_card(self, member: discord.Member) -> discord.File:
        db = mclient.bowser.users
        fs = gridfs.GridFS(mclient.bowser)
        dbUser = db.find_one({'_id': member.id})
        guild = member.guild

        metaFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 36)
        userFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 48)
        subtextFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 48)
        mediumFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 36)
        smallFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 30)

        # Start construction of key features
        pfp = Image.open(io.BytesIO(await member.avatar_url_as(format='png', size=256).read())).convert("RGBA").resize((250, 250))
        pfpBack = Image.open('resources/pfp-background.png').convert('RGBA')
        pfpBack.paste(pfp, (50, 170), pfp)
        card = Image.open('resources/profile-{}.png'.format(dbUser['background'])).convert("RGBA")
        pfpBack.paste(card, mask=card)
        card = pfpBack
        snoo = Image.open('resources/snoo.png').convert("RGBA")
        trophyUnderline = Image.open('resources/trophy-case-underline.png').convert("RGBA")
        gameUnderline = Image.open('resources/favorite-games-underline.png').convert("RGBA")

        card.paste(snoo, (50, 50), snoo)
        card.paste(trophyUnderline, (1150, 100), trophyUnderline)
        card.paste(gameUnderline, (60, 645), gameUnderline)

        # Start header/static text
        draw = ImageDraw.Draw(card)
        draw.text((150, 50), '/r/NintendoSwitch Discord', (45, 45, 45), font=metaFont)
        draw.text((150, 90), 'User Profile', (126, 126, 126), font=metaFont)
        draw.text((60, 470), 'Member since', (126, 126, 126), font=smallFont)
        draw.text((440, 470), 'Messages sent', (126, 126, 126), font=smallFont)
        draw.text((800, 470), 'Timezone', (126, 126, 126), font=smallFont)
        draw.text((60, 595), 'Favorite games', (45, 45, 45), font=mediumFont)
        #draw.text((800, 600), 'Looking for group', (126, 126, 126), font=smallFont) # TODO: Find a way to see if game is online enabled
        draw.text((1150, 45), 'Trophy case', (45, 45, 45), font=mediumFont)

        # Start customized content -- userinfo
        memberName = ''
        nameW = 350
        nameH = 0
        for char in member.name:
            if char not in UNICODE_EMOJI:
                memberName += char

            else:
                if memberName:
                    W, nameH = draw.textsize(memberName, font=userFont)
                    draw.text((nameW, 215), memberName, (80, 80, 80), font=userFont)
                    nameW += W
                    memberName = ''

                charset = tuple(codepoints.from_unicode(char))
                unicodePoint = []
                for x in charset:
                    unicodePoint.append(hex(x)[2:])

                unicodeChar = '-'.join(unicodePoint)
                emojiPic = Image.open(self.twemojiPath + unicodeChar + '.png').convert('RGBA').resize((40, 40))
                card.paste(emojiPic, (nameW + 3, 228), emojiPic)
                nameW += 46

        if memberName: # Leftovers, text
            draw.text((nameW, 215), memberName, (80, 80, 80), font=userFont)

        draw.text((350, 275), '#' + member.discriminator, (126, 126, 126), font=subtextFont)

        if dbUser['regionFlag']:
            regionImg = Image.open(self.twemojiPath + dbUser['regionFlag'] + '.png').convert('RGBA')
            card.paste(regionImg, (976, 50), regionImg)

        # Friend code
        if dbUser['friendcode']:
            draw.text((350, 330), dbUser['friendcode'], (87, 111, 251), font=subtextFont)

        # Start customized content -- stats
        draw.text((440, 505), f'{mclient.bowser.messages.find({"author": member.id}).count():,}', (80, 80, 80), font=mediumFont)

        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.datetime.utcfromtimestamp(joins[0])
        try: # -d doesn't work on all platforms, such as Windows
            joinDateF = joinDate.strftime('%b. %-d, %Y')
        except:
            joinDateF = joinDate.strftime('%b. %d, %Y')
        draw.text((60, 505), joinDateF, (80, 80, 80), font=mediumFont)

        if not dbUser['timezone']:
            draw.text((800, 505), 'Not specified', (126, 126, 126), font=mediumFont)

        else:
            tzOffset = datetime.datetime.now(pytz.timezone(dbUser['timezone'])).strftime('%z')
            draw.text((800, 505), 'GMT' + tzOffset, (80, 80, 80), font=mediumFont)

        # Start trophies
        trophyLocations = {
            0: (1150, 150),
            1: (1300, 150),
            2: (1450, 150),
            3: (1150, 300),
            4: (1300, 300),
            5: (1450, 300),
            6: (1150, 450),
            7: (1300, 450),
            8: (1450, 450),
            9: (1150, 600),
            10: (1300, 600),
            11: (1450, 600),
            12: (1150, 750),
            13: (1300, 750),
            14: (1450, 750)
        }
        trophies = []
        if dbUser['trophyPreference']:
            for x in dbUser:
                trophies.append(x)

        # Hardcoding IDs like a genius
        if member.id == guild.owner.id: # Server owner
            trophies.append('owner')

        app_info = await self.bot.application_info()

        if member.id in [app_info.owner.id, 123879073972748290]: # Developer
            trophies.append('developer')

        if guild.get_role(config.chatmod) in member.roles: # Chat-mod role
            trophies.append('chat-mod')

        if guild.get_role(config.submod) in member.roles: # Sub-mod role
            trophies.append('sub-mod')

        if guild.get_role(config.modemeritus) in member.roles or guild.get_role(config.submodemeritus) in member.roles: # Mod emeritus
            trophies.append('mod-emeritus')

        if guild.get_role(config.helpfulUser) in member.roles: # Helpful user
            trophies.append('helpful-user')

        if guild.get_role(config.boostRole) in member.roles: # Booster role
            trophies.append('booster')

        if len(trophies) < 15: # Check for additional non-prefered trophies
            for x in dbUser['trophies']:
                if x not in trophies: trophies.append(x)

        while len(trophies) < 15:
            trophies.append('blank')

        trophyNum = 0
        for x in trophies:
            trophyBadge = Image.open('resources/trophies/' + x + '.png').convert('RGBA')
            card.paste(trophyBadge, trophyLocations[trophyNum], trophyBadge)
            trophyNum += 1

        # Start favorite games
        setGames = dbUser['favgames']
        if not setGames:
            draw.text((60, 665), 'Not specified', (126, 126, 126), font=mediumFont)
        
        else:
            gameIconLocations = {
                0: (60, 665),
                1: (60, 730),
                2: (60, 795)
            }
            gameTextLocations = {
                0: 660,
                1: 725,
                2: 791
            }
            gameCount = 0
            gamesDb = mclient.bowser.games
            for game in setGames:
                gameDoc = gamesDb.find_one({'_id': game})
                if fs.exists(game):
                    gameImg = fs.get(game)
                    gameIcon = Image.open(gameImg).convert('RGBA').resize((45, 45))
                    card.paste(gameIcon, gameIconLocations[gameCount], gameIcon)

                else:
                    missingImage = Image.open('resources/missing-game.png').convert("RGBA").resize((45, 45))
                    card.paste(missingImage, gameIconLocations[gameCount], missingImage)

                if gameDoc['titles']['NA']:
                    gameName = gameDoc['titles']['NA']

                elif gameDoc['titles']['EU']:
                    gameName = gameDoc['titles']['EU']

                else:
                    gameName = gameDoc['titles']['JP']

                nameW = 120
                nameWMax = 950

                for char in gameName:
                    if nameW >= nameWMax:
                        draw.text((nameW, gameTextLocations[gameCount]), '...', (80, 80, 80), font=mediumFont)
                        break

                    draw.text((nameW, gameTextLocations[gameCount]), char, (80, 80, 80), font=mediumFont)
                    nameW += mediumFont.getsize(char)[0]
                gameCount += 1

        bytesFile = io.BytesIO()
        card.save(bytesFile, format='PNG')
        return discord.File(io.BytesIO(bytesFile.getvalue()), filename='profile.png')

    @_profile.command(name='edit')
    async def _profile_edit(self, ctx):
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': ctx.author.id})
        mainMsg = None

        if ctx.guild.get_role(config.moderator) not in ctx.author.roles and ctx.channel.id != config.commandsChannel: # commands
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} Please use bot commands in <#{config.commandsChannel}>, not {ctx.channel.mention}', delete_after=15)

        if ctx.author.id in self.inprogressEdits.keys() and (time.time() - self.inprogressEdits[ctx.author.id]) < 300:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You are already editing your profile! Please finish or wait a few minutes before trying again', delete_after=15)

        headerBase = 'Just a heads up! You can skip any section you do not want to edit right now by responding `skip` instead. Just edit your profile again to set it at a later time.'
        phase1 = 'What is your Nintendo Switch friend code? It looks like this: `SW-XXXX-XXXX-XXXX`'
        phase2 = 'What is the regional flag emoji for your country? Send a flag emoji like this: 🇺🇸'
        phase3 = 'What is your timezone region? You can find a list of regions here if you aren\'t sure: <http://www.timezoneconverter.com/cgi-bin/findzone.tzc>. For example, `America/New_York`'
        phase4 = 'Choose up to three (3) of your favorite games in total. You\'ve set {} out of 3 games so far. Send the title of a game as close to exact as possible, such as `1-2-Switch`'
        phase5 = 'Choose the background theme you would like to use for your profile. You have access to use the following themes: {}'

        # Lookup tables of values dependant on if user has setup their profile
        header = {
            True: f'{headerBase} If you would like to instead reset a section of your profile that you have previously set, just respond `reset` to any prompt.\n\n',
            False: f'{headerBase}\n\n'
        }

        embedText = { 
            'title': {
                True: 'Edit your user profile',
                False: 'Setup your user profile'
            },
            'descBase': {
                True: 'Welcome back to profile setup.',
                False: 'It appears you have not setup your profile before, let\'s get that taken care of!'
            }
        }

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == mainMsg.channel.id

        async def _phase1(message):
            response = await self.bot.wait_for('message', timeout=120, check=check)

            content = response.content.lower().strip()
            if response.content.lower().strip() == 'skip': return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **friend code**')
                return True

            code = re.search(self.friendCodeRegex['profile'], content)
            if code: # re match
                friendcode = f'SW-{code.group(1)}-{code.group(2)}-{code.group(3)}'
                db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': friendcode}})
                return True

            else:
                return False
                   
        async def _phase2(message):
            response = await self.bot.wait_for('message', timeout=120, check=check)

            content = response.content.strip()
            if response.content.lower().strip() == 'skip': return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'regionFlag': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **regional flag**')
                return True

            for x in content:
                if x not in UNICODE_EMOJI: return False

            rawPoints = tuple(codepoints.from_unicode(content))
            points = []

            for x in rawPoints:
                if str(hex(x)[2:]) not in self.letterCodepoints: # Flags are the 2 letter abbrev. in regional letter emoji
                    return False

                points.append(str(hex(x)[2:]))

            pointStr = '-'.join(points)
            if not Path(f'{self.twemojiPath}{pointStr}.png').is_file():
                return False

            db.update_one({'_id': ctx.author.id}, {'$set': {'regionFlag': pointStr}})
            return True

        async def _phase3(message):
            response = await self.bot.wait_for('message', timeout=300, check=check)

            content = response.content.lower().strip()
            if response.content.lower().strip() == 'skip': return True
            if response.content.lower().strip() == 'reset':
                db.update_one({'_id': ctx.author.id}, {'$set': {'timezone': None}})
                await message.channel.send('I\'ve gone ahead and reset your setting for **timezone**')
                return True

            for x in pytz.all_timezones:
                if content == x.lower():
                    db.update_one({'_id': ctx.author.id}, {'$set': {'timezone': x}})
                    return True

            return False

        async def _phase4(message):
            gameCnt = 0
            failedFetch = False
            userGames = []
            while gameCnt < 3:
                if failedFetch: await message.channel.send(f'{config.redTick} Hmm, I can\'t add that game. Make sure you typed the game name correctly and don\'t add the same game twice.\n\n' + phase4.format(gameCnt))
                else: await message.channel.send(phase4.format(gameCnt))
                failedFetch = False

                response = await self.bot.wait_for('message', timeout=180, check=check)
                if response.content.lower().strip() == 'skip': break
                if response.content.lower().strip() == 'reset':
                    db.update_one({'_id': ctx.author.id}, {'$set': {'favgames': []}})
                    await message.channel.send('I\'ve gone ahead and reset your setting for **favorite games**')
                    return True

                content = response.content.lower().strip()

                NintenDeals = self.bot.get_cog('Game Commands')
                if not NintenDeals.gamesReady:
                    waitMsg = await message.channel.send(f'{config.loading} Please wait a few moments, getting info on that game')
                    while not NintenDeals.gamesReady:
                        await asyncio.sleep(0.5)

                    await waitMsg.delete()

                games = NintenDeals.games

                gameObj = None
                titleList = {}

                for gameEntry in games.values():
                    for title in gameEntry['titles'].values():
                        if not title or title in titleList.keys(): continue
                        titleList[title] = gameEntry['_id']

                results = process.extract(content, titleList.keys(), limit=2)
                if results and results[0][1] >= 86:
                    if gameCnt == 0 and dbUser['favgames']: db.update_one({'_id': ctx.author.id}, {'$set': {'favgames': []}})
                    while True:
                        await message.channel.send(f'Is **{results[0][0]}** the game you are looking for? Type __yes__ or __no__')
                        checkResp = await self.bot.wait_for('message', timeout=120, check=check)
                        if checkResp.content.lower().strip() in ['yes', 'y']:
                            gameObj = games[titleList[results[0][0]]]
                            if gameObj['_id'] in userGames:
                                failedFetch = True
                                break

                            db.update_one({'_id': ctx.author.id}, {'$push': {'favgames': gameObj['_id']}})
                            gameCnt += 1
                            userGames.append(gameObj['_id'])
                            break

                        elif checkResp.content.lower().strip() in ['no', 'n']:
                            break

                else:
                    failedFetch = True

        async def _phase5(message):
            backgrounds = list(dbUser['backgrounds'])
            backgrounds.remove('default')

            if not backgrounds:
                await message.channel.send('Since you don\'t have any background themes unlocked we\'ll skip this step')
                return True

            else:
                backgrounds = list(dbUser['backgrounds'])
                await message.channel.send(phase5.format(', '.join(backgrounds)))
                while True:
                    response = await self.bot.wait_for('message', timeout=120, check=check)

                    content = response.content.lower().strip()
                    if response.content.lower().strip() == 'reset':
                        db.update_one({'_id': ctx.author.id}, {'$set': {'background': 'default'}})
                        await message.channel.send('I\'ve gone ahead and reset your setting for **profile background**')
                        return True

                    elif content != 'skip':
                        if content in backgrounds:
                            db.update_one({'_id': ctx.author.id}, {'$set': {'background': content}})
                            break

                        else:
                            await message.channel.send(f'{config.redTick} That background name doesn\'t look right. Make sure to send one of the options given.\n\n' + phase5.format(', '.join(backgrounds)))

                    else:
                        break


        profileSetup = dbUser['profileSetup']

        embed = discord.Embed(title=embedText['title'][profileSetup], description=embedText["descBase"][profileSetup] + \
                                '\nYou can customize the following values:\n\n･ Your Nintendo Switch friend code\n･ The regional flag for your country' \
                                '\n･ Your timezone\n･ Up to three (3) of your favorite Nintendo Switch games\n･ The background theme of your profile' \
                                '\n\nWhen prompted, simply reply with what you would like to set the field as.')
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)

        try:
            mainMsg = await ctx.author.send(embed=embed)
            self.inprogressEdits[ctx.author.id] = time.time()
            await ctx.message.add_reaction('📬')
            private = True

        except discord.Forbidden: # DMs not allowed, try in channel
            private = False
            return await ctx.send(f'{config.redTick} {ctx.author.mention} To edit your profile you\'ll need to open your DMs. I was unable to message you')
            mainMsg = await ctx.send(ctx.author.mention, embed=embed)

        if not profileSetup:
            db.update_one({'_id': ctx.author.id}, {'$set': {'profileSetup': True}})

        botMsg = await mainMsg.channel.send(header[profileSetup] + phase1)
        try:
            # Phase 1
            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase1(botMsg):
                    botMsg = await botMsg.channel.send(f'{config.redTick} That friend code doesn\'t look right.\n\n' + phase1)

                else:
                    phaseSuccess = True

            # Phase 2
            await botMsg.channel.send(phase2)

            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase2(botMsg):
                    botMsg = await botMsg.channel.send(f'{config.redTick} That emoji doesn\'t look right. Make sure you send only a flag emoji.\n\n' + phase2)

                else:
                    phaseSuccess = True

            # Phase 3
            await botMsg.channel.send(phase3)

            phaseStart = time.time()
            phaseSuccess = False
            while not phaseSuccess:
                if not await _phase3(botMsg):
                    botMsg = await botMsg.channel.send(f'{config.redTick} That timezone doesn\'t look right. Make sure you send the timezone area exactly. If you are having trouble, ask a moderator for help or skip this part.\n\n' + phase3)

                else:
                    phaseSuccess = True


            phaseStart = time.time()
            phaseSuccess = False

            # Phase 4
            phaseStart = time.time()
            phaseSuccess = False
            await _phase4(botMsg)

            # Phase 5
            phaseStart = time.time()
            phaseSuccess = False
            await _phase5(botMsg)

            del self.inprogressEdits[ctx.author.id]
            card = await self._generate_profile_card(ctx.author)
            return await mainMsg.channel.send('You are all set! Your profile has been edited:', file=card)

        except asyncio.TimeoutError:
            await mainMsg.delete()
            del self.inprogressEdits[ctx.author.id]
            return await botMsg.edit(content=f'{ctx.author.mention} You have taken too long to respond and the edit has been timed out, please run `!profile edit` to start again')

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.type != discord.ChannelType.text or message.author.bot:
            return

        content = re.sub(r'(<@!?\d+>)', '', message.content)
        contains_code = utils.re_match_nonlink(self.friendCodeRegex['chatFilter'], content)

        if not contains_code: return
        if message.channel.id not in [config.commandsChannel, config.voiceTextChannel]:
            await message.channel.send(f'{message.author.mention} Hi! It appears you\'ve sent a **friend code**. An easy way to store and share your friend code is with our server profile system. To view your profile use the `!profile` command. To set details such as your friend code on your profile, use `!profile edit` in <#{config.commandsChannel}>. You can even see the profiles of other users with `!profile @user`')

    @_profile.error
    async def social_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name

        if isinstance(error, commands.CommandOnCooldown):
            if cmd_str == 'profile' and (ctx.message.channel.id in [config.commandsChannel, config.voiceTextChannel] or ctx.guild.get_role(config.moderator) in ctx.author.roles):
                await self._profile.__call__(ctx, None if not ctx.args else ctx.args[0])
            else:
                return await ctx.send(f'{config.redTick} That command is being used too often, try again in a few seconds.', delete_after=15)

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.', delete_after=15)
            raise error

def setup(bot):
    bot.add_cog(SocialFeatures(bot))
    logging.info('[Extension] Social module loaded')

def teardown(bot):
    bot.remove_cog('SocialFeatures')
    logging.info('[Extension] Social module unloaded')
