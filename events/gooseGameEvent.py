import asyncio
import aiohttp
import logging
import random
import io
import urllib.request

from discord.ext import commands
from discord import Webhook, AsyncWebhookAdapter, File, Embed, NotFound
import pymongo

import config

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class GooseGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gooseMessages = {}
        self.goosePicture = 'https://cdn2.unrealengine.com/Diesel%2Fproduct%2Fflour%2Fhome%2FEGS_HouseHouse_UntitledGooseGame_G2-1000x840-65d06d290e00ba237c4364481c7776280ef3bf27.png'
        self.gooseTypes = {
            'one': [
                'https://www.goose.game/presskit/screenshots/goose_screenshot-03.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-06.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-07.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-13.png'
            ],
            'two': [
                'https://www.goose.game/presskit/screenshots/goose_screenshot-08.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-04.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-02.png'
            ],
            'three': [
                'https://www.goose.game/presskit/screenshots/goose_screenshot-14.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-11.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-05.png'
            ],
            'four': [
                'https://www.goose.game/presskit/screenshots/goose_screenshot-12.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-09.png',
                'https://www.goose.game/presskit/screenshots/goose_screenshot-10.png'
            ],
            'five': [
                'https://www.goose.game/presskit/screenshots/goose_screenshot-01.png'
            ]
        }
        self.gooseNumberInts = {
            'one': 1,
            'two': 2,
            'three': 3,
            'four': 4,
            'five': 5
        }
        self.gooseEmotes = [
            '<:srfetched:624356488395227136>',
            '<:goose:623968870805405753>',
            '<:swan:624357453324091412>',
            '🦆',
            '🐓'
        ]

    async def calculate_place(self, user=None):
        db = mclient.bowser.gooseEvent.find({}).sort('points', pymongo.DESCENDING)
        rankings = {}
        place = 0

        for x in db:
            place += 1
            rankings[place] = {'user': x['_id'], 'points': x['points']}

        if user:
            for key, value in rankings.items():
                if value['user'] == user:
                    return key, value['points']

        else:
            return rankings

    @commands.group(name='event')
    async def _event(self, ctx):
        return

    @_event.command(name='points')
    async def _group_points(self, ctx):
        db = mclient.bowser.gooseEvent
        goose = self.bot.get_channel(624221034194665482)
        if ctx.channel.id != 624221034194665482:
            await ctx.message.delete()

        points = db.find_one({'_id': ctx.author.id})
        if not points:
            return await goose.send(f'{ctx.author.mention}, you don\'t have any points! Find some geese, and scare them off!')

        placing = await self.calculate_place(ctx.author.id)
        await goose.send(f'{ctx.author.mention}, You have {points["points"]} points and are #{placing[0]} on the leaderboard! Keep up the good work, and scare off more geese!')

    @_event.command(name='leaderboard')
    async def _group_leaderboard(self, ctx):
        goose = self.bot.get_channel(624221034194665482)
        if ctx.channel.id != 624221034194665482:
            await ctx.message.delete()

        embed = Embed(title='Leaderboard', description='Here is the current standings all geese finders', color=0x4A90E2)
        embed.set_thumbnail(url='https://catwithmonocle.com/wp-content/uploads/2019/08/featured-untitledgoosegame.jpg')
        rankings = await self.calculate_place()

        maxEntries = 10
        for key, value in rankings.items():
            maxEntries -= 1
            if maxEntries < 0:
                break

            try:
                user = self.bot.get_user(value['user'])

            except NotFound: # Left server
                user = await self.bot.fetch_user(value['user'])

            points = f'__{value["points"]} points__' if value['points'] > 1 else '__1 point__'
            embed.add_field(name=f'#{key}', value=f'{points} - {user}')

        if maxEntries == 10:
            # No scores yet
            embed.add_field(name='Scores', value='Hm, it looks like no one has a score yet. User scores will show up here after at least one person scores a point. Keep an eye out for geese!')

        return await goose.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.channel.id not in [238081135865757696, 238080668347662336, 238081280632160257, 624221034194665482]:
            return # discussion, gaming, offtopic, goose

        if random.choices(['y', 'n'], weights=[1.75, 98.25])[0] == 'n':
            return

        pointValue = random.choices(['one', 'two', 'three', 'four', 'five'], weights=[40, 25, 20, 10, 5])[0]

        image = random.choices(self.gooseTypes[pointValue])[0]
        with urllib.request.urlopen(image) as url:
            imageFile = File(io.BytesIO(url.read()), filename='goosescene.png')

        gameMessage = await message.channel.send(content='A wild goose is attacking! First person to react with <:goose:623968870805405753> will chase it away!', file=imageFile)
        self.gooseMessages[gameMessage.id] = pointValue

        random.shuffle(self.gooseEmotes)

        for x in self.gooseEmotes:
            await gameMessage.add_reaction(x)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot: return # No bot reactions (i.e. us) should be counted

        if reaction.message.id in self.gooseMessages.keys() and reaction.emoji.id == 623968870805405753:
            gameMessage = self.gooseMessages[reaction.message.id]
            del self.gooseMessages[reaction.message.id]

            db = mclient.bowser.gooseEvent
            if not db.find_one_and_update({'_id': user.id}, {'$inc': {'points': self.gooseNumberInts[gameMessage]}}):
                db.insert_one({
                    '_id': user.id,
                    'points': self.gooseNumberInts[gameMessage]
                })

            points = f'**{gameMessage}** points' if gameMessage != 'one' else '**one** point'
            await reaction.message.edit(content=f'{user.mention} chased the goose away and gained {points}!')
            await reaction.message.clear_reactions()

def setup(bot):
    bot.add_cog(GooseGame(bot))
    logging.info('[Extension] gooseGameEvent module loaded')

def teardown(bot):
    bot.remove_cog('GooseGame')
    logging.info('[Extension] gooseGameEvent module unloaded')
