import asyncio
import typing
import io
import logging
import re
import datetime

import numpy as np
import pymongo
import gridfs
import requests
import discord
from discord.ext import commands
from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw
from emoji import UNICODE_EMOJI
from codepoints import from_unicode

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class SocialFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.friendcodeRe = re.compile(r'(?:SW)?-?(\d{4})[ -]?(\d{4})[ -]?(\d{4})', re.I)

    @commands.group(name='profile', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)#, 585536225725775893, 283753284483809280)
    async def _profile(self, ctx, member: typing.Optional[discord.Member]):
        if not member: member = ctx.author
        db = mclient.bowser.users
        fs = gridfs.GridFS(mclient.bowser)
        dbUser = db.find_one({'_id': member.id})

        metaFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 36)
        userFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 48)
        subtextFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 48)
        mediumFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 36)
        smallFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 30)

        # Start construction of key features
        pfp = Image.open(io.BytesIO(await member.avatar_url_as(format='png', size=256).read())).convert("RGBA").resize((250, 250))
        pfpBack = Image.open('resources/pfp-background.png').convert('RGBA')
        pfpBack.paste(pfp, (50, 170), pfp)
        card = Image.open('resources/profile-default.png').convert("RGBA")
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

                charset = tuple(from_unicode(char))
                unicodePoint = []
                for x in charset:
                    unicodePoint.append(hex(x)[2:])

                unicodeChar = '-'.join(unicodePoint)
                emojiPic = Image.open('resources/twemoji/' + unicodeChar + '.png').convert('RGBA').resize((40, 40))
                card.paste(emojiPic, (nameW + 3, 228), emojiPic)
                nameW += 46

        if memberName: # Leftovers, text
            draw.text((nameW, 215), memberName, (80, 80, 80), font=userFont)

        draw.text((350, 275), '#' + member.discriminator, (126, 126, 126), font=subtextFont)

        if dbUser['regionFlag']:
            regionImg = Image.open('resources/twemoji/' + dbUser['regionFlag'] + '.png').convert('RGBA')
            card.paste(regionImg, (976, 50), regionImg)

        # Friend code
        if dbUser['friendcode']:
            draw.text((350, 330), dbUser['friendcode'], (87, 111, 251), font=subtextFont)

        # Start customized content -- stats
        draw.text((440, 505), f'{mclient.bowser.messages.find({"author": member.id}).count():,}', (80, 80, 80), font=mediumFont)

        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.datetime.utcfromtimestamp(joins[0])
        draw.text((60, 505), joinDate.strftime('%b. %-d, %Y'), (80, 80, 80), font=mediumFont)

        if not dbUser['timezone']:
            draw.text((800, 505), 'Not specified', (126, 126, 126), font=mediumFont)

        else:
            draw.text((800, 505), dbUser['timezone'], (80, 80, 80), font=mediumFont)

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

        if ctx.guild.get_role(585536225725775893) in member.roles and 'booster' not in trophies:
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
                    websites = gameDoc['websites']
                    siteUrl = None
                    if websites['US']: siteUrl = websites['US']
                    elif websites['CA']: siteUrl = websites['CA']
                    else:
                        continue # No non-NA games yet
                    #elif websites['EU']: siteUrl = websites['EU']
                    #elif websites['GB']: siteUrl = websites['GB']
                    #elif websites['AU']: siteUrl = websites['AU']
                    #elif websites['NZ']: siteUrl = websites['NZ']
                    #elif websites['JP']: siteUrl = websites['JP']
                    #elif websites['CH']: siteUrl = websites['CH']
                    #elif websites['RU']: siteUrl = websites['RU']
                    #elif websites['ZA']: siteUrl = websites['ZA']

                    try:
                        gameScrape = await utils.scrape_nintendo(siteUrl)

                    except:
                        return await ctx.send(f'{config.redTick} An unexpected error has occured while getting game data for the profile, please try again later')

                    r = requests.get(gameScrape['image'], stream=True)
                    if r.status_code != 200:
                        return await ctx.send(f'{config.redTick} An unexpected error has occured while getting game data for the profile, please try again later')

                    fs.put(r.raw, _id=game)
                    gameIcon = Image.open(fs.get(game)).convert('RGBA').resize((45, 45))
                    card.paste(gameIcon, gameIconLocations[gameCount], gameIcon)

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
        await ctx.send(file=discord.File(io.BytesIO(bytesFile.getvalue()), filename='profile.png'))

    @_profile.command(name='edit')
    async def _profile_edit(self, ctx, code):
        # People are sneaky and guessing command syntax for unreleased features
        sneaky = True
        for role in ctx.author.roles:
            if role.id == 263764663152541696:
                sneaky = False
                break

        if sneaky:
            import random
            return await ctx.send(random.choice(['lol no.', 'being sneaky eh?', 'how do you know about a undocumented command?', 'nah bro.', f'{config.redTick} Error: Suc']))

        embed = discord.Embed(title='Edit profile information', description='Please click the reaction of the option you would like to edit:\n\n1️⃣ Friend code')
        db = mclient.bowser.users
        db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': code}})
        return await ctx.send(db.find_one({'_id': ctx.author.id}))

    @commands.Cog.listener()
    async def on_message(self, message):
        code = re.search(self.friendcodeRe, message.clean_content)
        if not code: return

        if message.channel.id == 278544122661306369: # friend-code-bot
            pass

def setup(bot):
    bot.add_cog(SocialFeatures(bot))
    logging.info('[Extension] Social module loaded')

def teardown(bot):
    bot.remove_cog('SocialFeatures')
    logging.info('[Extension] Social module unloaded')
