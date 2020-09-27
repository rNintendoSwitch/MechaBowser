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
activeEmoji = """
<:spacer:754356052387954748><:spacer:754356052387954748><a:Mario_Brick_Top:753445692587900960>
<:spacer:754356052387954748><:spacer:754356052387954748><a:Mario_Brick_Below_Top:753441662960664626>
<:brick:754354293410365440><:brick:754354293410365440><a:Mario_Brick_Above_Bottom:753441663870959666><:brick:754354293410365440>
<:foot_brick:754355690205872199><:foot_brick:754355690205872199><a:Mario_Brick_Bottom:753441663481020447><:foot_brick:754355690205872199>
"""
inactiveEmoji = """
<:spacer:754356052387954748><:spacer:754356052387954748>
<:spacer:754356052387954748><:spacer:754356052387954748>
<:brick:754354293410365440><:brick:754354293410365440><:done_brick:754361741500088342><:brick:754354293410365440>
<:foot_brick:754355690205872199><:foot_brick:754355690205872199><:mario_standalone:754363810017574962><:foot_brick:754355690205872199>
"""
coinEmoji = '<:mariocoin:757300710894207067>'
class MarioGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gameMessages = {}
        self.shopChannel = self.bot.get_channel(757411216774791189)

    async def calculate_place(self, user=None):
        db = mclient.bowser.mario35Event.find({}).sort('coins', pymongo.DESCENDING)
        rankings = {}
        place = 0

        for x in db:
            place += 1
            rankings[place] = {'user': x['_id'], 'coins': x['coins']}

        if user:
            for key, value in rankings.items():
                if value['user'] == user:
                    return key, value['coins']

        else:
            return rankings

    @commands.is_owner()
    @commands.command(name='pricepost')
    async def _pricepost(self, ctx):
        await ctx.message.delete()
        textPost = f"Hello! Hi! I have some cool wares for your all-star adventure! Take a look!\n__All items are limit 1 per user__\n\n\n20 <:mariocoin:757300710894207067> - `galaxy-profile` - *Super Mario Galaxy Profile Background*\n20 <:mariocoin:757300710894207067> - `sunshine-profile` - *Super Mario Sunshine Profile Background*\n20 <:mariocoin:757300710894207067> - `mario64-profile` - *Super Mario 64 Profile Background*\n20 <:mariocoin:757300710894207067> - `allstars-profile` - *Super Mario 3D All-stars Profile Background*\n\n40 <:mariocoin:757300710894207067> - `ticket` - *Raffle ticket for giveaway*\n\n\nA giveaway? What is it?! The /r/NintendoSwitch Discord is giving away __2 physical copies__ of **Super Mario 3D All-stars** (US Only)! Extra deets:\n\nLimit one (1) raffle ticket for giveaway entry per-user; no purchase required. One (1) winner will receive a physical copy of **Super Mario 3D All-stars** and an exclusive **Magnet Set**. One winner (1) will receive a physical copy of **Super Mario All-stars** without a Magnet Set."
        embed = Embed(title='Toad Outpost', color=0xFFF62D, description=textPost)
        embed.add_field(name='How do I buy something?', value='Easy! Run the `!buy item` command in this channel, replacing "item" with what you would like to buy. The item name is the highlighted section for each item in the store. For example, if you wanted to buy the **Super Mario 3D All-stars Profile Background**, run `!buy allstars-profile`.')
        embed.set_thumbnail(url='https://www.geekycostumeideas.com/wp-content/uploads/2016/08/toad_super_mario.png')
        await self.shopChannel.send(embed=embed)

    @commands.group(name='event')
    async def _event(self, ctx):
        return

    @commands.command(name='buy')
    async def _buy(self, ctx, item):
        await ctx.message.delete()
        if ctx.channel.id != 757411216774791189: return await ctx.send(f'{config.redTick} {ctx.author.mention} You can only use this command in <#757411216774791189>', delete_after=10)
        items = ['galaxy-profile', 'sunshine-profile', 'mario64-profile', 'allstars-profile', 'ticket']
        item = item.lower()

        event = mclient.bowser.mario35Event
        status = event.find_one({'_id': ctx.author.id})
        if not status:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any coins yet! Keep a look out for coin messages in <#238081280632160257>, <#238081135865757696>, and <#325430144993067049> to get some before trying to buy items!')

        if item not in items:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} Hmm, I don\'t recognize "{item}" as an item for sale', delete_after=10)

        if item[-7:] == 'profile':
            db = mclient.bowser.users
            user = db.find_one({'_id': ctx.author.id})
            if item[:-8] in user['backgrounds']:
                return await ctx.send(f'{config.redTick} {ctx.author.mention} You already have that background! To set it as your background, use the `!profile edit` command in <#670999043740270602>', delete_after=10)

            if status['coins'] < 20:
                return await ctx.send(f'{config.redTick} {ctx.author.mention} You do not have enough coins to get that!', delete_after=10)

            event.update_one({'_id': ctx.author.id}, {'$inc': {'coins': -20}})
            db.update_one({'_id': ctx.author.id}, {'$push': {'backgrounds': item[:-8]}})
            return await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You bought the **{item[:-8]} profile background**. To set it as your current background, use the `!profile edit` command in <#670999043740270602>', delete_after=20)

        else: # Raffle ticket
            try:
                entered = status['raffle']

            except KeyError:
                pass

            else:
                if entered: return await ctx.send(f'{config.redTick} {ctx.author.mention} You already have an entry ticket to the giveaway!', delete_after=10)

            if status['coins'] < 40:
                return await ctx.send(f'{config.redTick} {ctx.author.mention} You do not have enough coins to get that!', delete_after=10)

            event.update_one({'_id': ctx.author.id}, {'$inc': {'coins': -40}, '$set': {'raffle': True}})
            return await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You bought a **giveaway raffle ticket**.', delete_after=10)

    @_event.command(name='coins')
    async def _group_points(self, ctx):
        if ctx.channel.id != 758418138269483081:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use that command here! Try it in <#758418138269483081> instead', delete_after=10)

        db = mclient.bowser.mario35Event
        points = db.find_one({'_id': ctx.author.id})
        if not points:
            return await ctx.send(f'{ctx.author.mention}, you don\'t have any points! Look out for question boxes!')

        placing = await self.calculate_place(ctx.author.id)
        await ctx.send(f'{ctx.author.mention}, You have {points["coins"]} coins and are #{placing[0]} on the leaderboard! Keep up the good work, and hit more blocks!')

    @_event.command(name='leaderboard')
    async def _group_leaderboard(self, ctx):
        if ctx.channel.id != 758418138269483081:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use that command here! Try it in <#758418138269483081> instead', delete_after=10)

        embed = Embed(title='Leaderboard', description='Here is the current standing for all block punchers', color=0x4A90E2)
        embed.set_thumbnail(url='https://cdn.mattbsg.xyz/pQdl69rwbg.png')
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

            points = f'__{value["coins"]} coins__' if value['coins'] > 1 else '__1 coin__'
            embed.add_field(name=f'#{key}', value=f'{points}\n{user}')

        if maxEntries == 10:
            # No scores yet
            embed.add_field(name='Scores', value='Hm, it looks like no one has a score yet. User scores will show up here after at least one person scores a point. Keep an eye out for question blocks!')

        return await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.channel.id not in [238081280632160257, 238081135865757696, 325430144993067049, 276036563866091521]:
            return # general, switch-discussion, mario, debug

        if random.choices(['y', 'n'], weights=[0.5, 99.5])[0] == 'n':
            return

        embed = Embed(title='A question block has appeared!', color=0xe52521, description=activeEmoji)
        gameMessage = await message.channel.send('It\'s a me, Mario! Keep reacting with <:mariocoin:757300710894207067> to get upto 5 coins from the question block!', embed=embed)
        self.gameMessages[gameMessage.id] = {}
        print(self.gameMessages[gameMessage.id])
        loop = self.bot.loop
        loop.call_later(15, loop.create_task, self.end_game(gameMessage))

        await gameMessage.add_reaction(coinEmoji)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot: return # No bot reactions (i.e. us) should be counted
        if reaction.message.id in self.gameMessages.keys() and reaction.emoji.id == 757300710894207067:
            print(str(user))
            print('valid')
            if user.id in self.gameMessages[reaction.message.id].keys() and self.gameMessages[reaction.message.id][user.id] < 5:
                self.gameMessages[reaction.message.id][user.id] += 1

            elif not user.id in self.gameMessages[reaction.message.id].keys():
                self.gameMessages[reaction.message.id][user.id] = 1

            else: # Exists in dict, but not less than 8 coins
                return

    async def end_game(self, message):
        db = mclient.bowser.mario35Event
        embed = message.embeds[0]
        embed.description = inactiveEmoji
        await message.edit(content='Bye bye!', embed=embed)
        await message.clear_reactions()
        print(self.gameMessages[message.id])

        for key, value in self.gameMessages[message.id].items():
            db.update_one({'_id': key}, {'$inc': {
                'coins': value
            }}, upsert=True)

        del self.gameMessages[message.id]

def setup(bot):
    bot.add_cog(MarioGame(bot))
    logging.info('[Extension] MarioGame module loaded')

def teardown(bot):
    bot.remove_cog('MarioGame')
    logging.info('[Extension] MarioGame module unloaded')
