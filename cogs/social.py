import asyncio
import typing
import io
import logging
import re
import datetime

import numpy as np
import pymongo
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
        dbUser = db.find_one({'_id': member.id})
        metaFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 36)
        userFont = ImageFont.truetype('resources/OpenSans-Regular.ttf', 48)
        subtextFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 48)
        mediumFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 36)
        smallFont = ImageFont.truetype('resources/OpenSans-Light.ttf', 30)

        # Start construction of key features
        card = Image.open('resources/profile-default.png').convert("RGBA")
        snoo = Image.open('resources/snoo.png').convert("RGBA")
        profileBack = Image.open('resources/pfp-placeholder.png').convert("RGBA")
        trophyUnderline = Image.open('resources/trophy-case-underline.png').convert("RGBA")
        gameUnderline = Image.open('resources/favorite-games-underline.png').convert("RGBA")

        card.paste(snoo, (50, 50), snoo)
        card.paste(profileBack, (50, 170), profileBack)
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
        draw.text((800, 600), 'Looking for group', (126, 126, 126), font=smallFont)
        draw.text((1150, 45), 'Trophy case', (45, 45, 45), font=mediumFont)

        # Start customized content -- pfp
        pfp = Image.open(io.BytesIO(await member.avatar_url_as(format='png', size=256).read())).convert("RGBA").resize((227, 227))

        offset = 0
        mask = Image.new("L", pfp.size, 0)
        pfpDraw = ImageDraw.Draw(mask)
        pfpDraw.ellipse((offset, offset, pfp.size[0] - offset, pfp.size[1] - offset), fill=255)

        result = pfp.copy()
        result.putalpha(mask)

        card.paste(result, (61, 182), result)

        # Start customized content -- userinfo
        memberName = ''
        nameW = 350
        nameH = 0
        for char in member.name:
            if char not in UNICODE_EMOJI:
                memberName += char

            else:
                print('EMOJI')
                if memberName:
                    W, nameH = draw.textsize(memberName, font=userFont)
                    draw.text((nameW, 215), memberName, (80, 80, 80), font=userFont)
                    nameW += W
                    memberName = ''

                charset = tuple(from_unicode(char))
                unicodePoint = []
                for x in charset:
                    print(x)
                    print(hex(x))
                    unicodePoint.append(hex(x)[2:])

                unicodeChar = '-'.join(unicodePoint)
                emojiPic = Image.open('resources/twemoji/' + unicodeChar + '.png').convert('RGBA').resize((40, 40))
                card.paste(emojiPic, (nameW + 3, 228), emojiPic)
                nameW += 46

        if memberName: # Leftovers, text
            draw.text((nameW, 215), memberName, (80, 80, 80), font=userFont)

        draw.text((350, 275), '#' + member.discriminator, (126, 126, 126), font=subtextFont)

        # Friend code
        if dbUser['friendcode'] == None:
            friendcode = 'I haven\'t set a friend code yet!'

        else:
            friendcode = f'Switch Friend Code: {dbUser["friendcode"]}'

        draw.text((350, 330), friendcode, (87, 111, 251), font=subtextFont)

        # Start customized content -- stats
        draw.text((440, 505), f'{mclient.bowser.messages.find({"author": member.id}).count():,}', (80, 80, 80), font=mediumFont)

        joins = dbUser['joins']
        joins.sort()
        joinDate = datetime.datetime.utcfromtimestamp(joins[0])
        draw.text((60, 505), joinDate.strftime('%b. %-d, %Y'), (80, 80, 80), font=mediumFont)

        userTz = 'Unknown' if not dbUser['timezone'] else dbUser['timezone']
        draw.text((800, 505), userTz, (80, 80, 80), font=mediumFont)

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

        bytesFile = io.BytesIO()
        card.save(bytesFile, format='PNG')
        await ctx.send(file=discord.File(io.BytesIO(bytesFile.getvalue()), filename='profile.png'))

    @_profile.command(name='edit')
    async def _profile_edit(self, ctx, code):
        embed = discord.Embed(title='Edit profile imformation', description='Please click the reaction of the option you would like to edit:\n\n1️⃣ Friend code')
        db = mclient.bowser.users
        db.update_one({'_id': ctx.author.id}, {'$set': {'friendcode': code}})
        return await ctx.send(db.find_one({'_id': ctx.author.id}))

    @commands.Cog.listener()
    async def on_message(self, message):
        code = re.search(self.friendcodeRe, message.clean_content)
        if not code: return

        if message.channel.id == 278544122661306369: # friend-code-bot
            

def setup(bot):
    bot.add_cog(SocialFeatures(bot))
    logging.info('[Extension] Social module loaded')

def teardown(bot):
    bot.remove_cog('SocialFeatures')
    logging.info('[Extension] Social module unloaded')
