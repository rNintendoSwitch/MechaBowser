import asyncio
import typing
import io
import logging
import re

import numpy as np
import pymongo
import discord
from discord.ext import commands
from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw

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
    @commands.has_any_role(config.moderator, config.eh, 585536225725775893, 283753284483809280)
    async def _profile(self, ctx, member: typing.Optional[discord.Member]):
        if not member: member = ctx.author
        db = mclient.bowser.users
        dbUser = db.find_one({'_id': member.id})
        namefont = ImageFont.truetype('26141.otf', 48)
        subtextfont = ImageFont.truetype('26141.otf', 26)
        nonheaderfont = ImageFont.truetype('26141.otf', 32)

        #return print(await member.avatar_url_as(format='png', size=256).read())
        # Profile icon
        pfp = Image.open(io.BytesIO(await member.avatar_url_as(format='png', size=256).read())).convert("RGBA").resize((256, 256))
        card = Image.open("profilecard.png")
        background = Image.new("RGBA", card.size, (255, 0, 0, 0))
        background.paste(pfp, (50, 185), pfp)
        background.paste(card, (0, 0), card)

        draw = ImageDraw.Draw(background)
        nameW, nameH = draw.textsize(member.name, font=namefont)
        discrimW, discrimH = draw.textsize('#', font=namefont)

        # Name data
        draw.text((358,185), member.name, (56,56,56), font=namefont) # Set coords starting point
        draw.text((358 + nameW,185), '#', (127,127,127), font=namefont) # Continue from end of name
        draw.text((358 + nameW + discrimW,185), member.discriminator, (127,127,127), font=namefont)

        # Friend code
        if dbUser['friendcode'] == None:
            friendcode = 'I have\'t set a friend code yet!'

        else:
            friendcode = f'Switch Friend Code: {dbUser["friendcode"]}'

        draw.text((372,248), friendcode, (50,80,255), font=subtextfont)

        # Member since
        draw.text((356,376), member.joined_at.strftime('%B %d, %Y'), (56,56,56), font=nonheaderfont)

        # Messages
        draw.text((776,376), f'{mclient.bowser.messages.find({"author": member.id}).count():,}', (56,56,56), font=nonheaderfont)

        bytesFile = io.BytesIO()
        background.save(bytesFile, format='PNG')
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
