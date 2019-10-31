import asyncio
import random
import logging
import datetime
import typing
import math


import pymongo
from discord.ext import commands, tasks
from discord import Embed, File, Member, NotFound

import config
import utils
from events.resources.lm3 import qte

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

class Mansion(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bars = 20
        self.gameChannel = self.bot.get_channel(276036563866091521)
        self.ghost = None
        self.floor = 1
        self.ghostTypes = {
            'ghosts': [
                {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_04.png', # Blue stick
                    'value': 15,
                    'hp': 20,#1500,
                    'odds': 60
                },
                {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_05.png', # Red square
                    'value': 25,
                    'hp': 20,#2000,
                    'odds': 30
                },
                {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_12.png', # Boo
                    'value': 35,
                    'hp': 20,#3000,
                    'odds': 10
                }
            ],
            'bosses': { # Boss ghosts - only ghost to have names to id them to users
                1: {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_08.png',
                    'value': 500,
                    'hp': 30000,
                    'name': 'Steward'
                },
                2: {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_09.png',
                    'value': 500,
                    'hp': 20000,
                    'name': 'Hellen Gravely'
                },
                3: {
                    'image': '/root/mecha-bowser/events/resources/lm3/Boolossus1.png',
                    'value': 500,
                    'hp': 20000,
                    'name': 'Boolossus'
                },
                4: {
                    'image': '/root/mecha-bowser/events/resources/lm3/Switch_LuigisMansion3_E3_artwork_14.png',
                    'value': 1000,
                    'hp': 50000,
                    'name': 'King Boo'
                }
            }
        }
        self.superSizedNames = [
            'Giant',
            'Mega-sized',
            'Larger-than-life',
            'Gigantic'
            'Super-sized'
        ]
        self.attackNames = [
            'stands in your way!',
            'blocks the way!',
            'appeared!',
            'snuck up from behind!',
            'appeared out of no where!',
            'jumped from behind a corner!',
            'came through the floor!'
        ]
        self.spacer = [
            '📍',
            '✅',
            '✔'
            '🔹',
            '🔸',
            '▶',
            '+',
            '>'
        ]
        self.superSize = None
        self.participants = {}
        self.poltergustEmote = '<:poltergust3000:636290175680380978>'
        self.booEmote = '<:Boo:638490699402182687>'
        self.booRave = '<a:BooRave:638490426889732155>'
        self.bootRaveRvs = '<a:BooRaveReverse:638490426638336023>'
        self.coin = '<:coin:638882119682228236>'
        self.coinRvs = '<:coinreverse:638859919793324032>'
        self.activeItems = []
        self.multiplier = 1
        self.coinMultiplier = False
        self.shopChannel = self.bot.get_channel(638872378545274900)
        self.items = {
            9: {'id': 'flower', 'name': 'pretty flower', 'price': 999999999},
            8: {'id': 'role', 'name': 'strange title plaque', 'price': 999999999},
            7: {'id': 'spirit', 'name': 'spirit ball', 'price': 30},
            6: {'id': 'banana', 'name': 'magnificent banana', 'price': 15},
            5: {'id': 'sauce', 'name': 'chef soulfflé\'s secret sauce', 'price': 20},
            4: {'id': 'summoner', 'name': 'magna-goo-tizer', 'price': 5},
            3: {'id': 'gooigi', 'name': 'goo-igi stand-in', 'price': 15},
            2: {'id': 'flashlight', 'name': 'interdimentional dark-light device', 'price': 10},
            1: {'id': 'bone', 'name': 'golden bone', 'price': 10}
        }
        self._make_ghost.start() #pylint: disable=no-member
        self._expire_effects.start() #pylint: disable=no-member
        logging.info('[Extension] LM Event extension loaded')

    def cog_unload(self):
        self._make_ghost.cancel() #pylint: disable=no-member
        self._expire_effects.cancel() #pylint: disable=no-member
        logging.info('[Extension] LM Event extension unloaded')

    @tasks.loop(minutes=2.0)
    async def _make_ghost(self):
        if self.ghost: return # If there is an active ghost, we don't want to continue
        if not random.choices([True, False], weights= [20, 80])[0]: return # Want a rare chance for a ghost to spawn

        await self.run_ghost()

    @tasks.loop(seconds=1)
    async def _expire_effects(self):
        try:
            for item in self.activeItems[:]:
                if item['expires'] and item['expires'] <= datetime.datetime.utcnow():
                    if item['id'] == 'gooigi':
                        self.multiplier -= 1

                    elif item['id'] == 'sauce':
                        self.coinMultiplier = False

                    print(f'removing {item["id"]}')
                    self.activeItems.remove(item)

                print(item)

        except Exception as e:
            print(e)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.channel.id not in [637351107999301633, 638872378545274900]: return

        if message.channel.id == 637351107999301633:
            await message.delete()

        if message.channel.id == 638872378545274900:
            if not message.content.startswith('!buy') or not message.content.startswith('!use') or not message.content.startswith('!backpack') or not message.content.startswith('!leaderboard'):
                await message.delete()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        db = mclient.bowser.lmEvent
        if user.bot: return
        if not self.ghost: return
        if reaction.message.id != self.ghost.id: return
        if str(reaction) != self.poltergustEmote: return

        dbUser = db.find_one({'user': user.id})
        multiplier = 1
        damage = 0 if not self.participants or not user.id in self.participants.keys() else self.participants[user.id]['damage']
        if not dbUser:
            db.insert_one({
                'user': user.id,
                'coins': 0,
                'level': 1,
                'inventory': {
                    '1': 0,
                    '2': 0,
                    '3': 0,
                    '4': 0,
                    '5': 0,
                    '6': 0,
                    '7': 0,
                    '8': 0,
                    '9': 0
                },
                'effects': {},
                'damage': 0
            })

        dbUser = db.find_one({'user': user.id})
        multiplier *= (1.1**(dbUser['level'] - 1))

        self.participants[user.id] = {'dps': 2, 'multiplier': multiplier, 'damage': damage, 'level': dbUser['level'], 'active': True, 'flags': []}

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
        if not self.ghost: return
        if reaction.message.id != self.ghost.id: return
        if str(reaction) != self.poltergustEmote: return

        if user.id in self.participants.keys():
            self.participants[user.id]['active'] = False

    @commands.is_owner()
    @commands.command(name='shoppost')
    async def percent_test(self, ctx):
        embed = Embed(title="The [un]Scientific Marketplace", colour=0xf6e1b8, description="He he he, so ya want to try some of my new fangled inventions and discoverys? Please go on ahead and take a look. I will need to take a restocking fee for each item however, and it tends to go up over time...\n\n**Interested in gettin' something?** Say `!buy 1`, replacing the \"1\" with the number item you want. Or don't change it and just get nice lil Polterpup a delicious *Golden bone*.\n\n\n")

        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/585528775471661244/638869884125708288/prof-e.gadd.png")
        embed.set_author(name="Prof. E. Gadd's Laboratory")
        embed.set_footer(text="Choose wisely... my wares may just save your life!")

        embed.add_field(name=f"1. Golden bone | {self.coin} 10", value="Oh that mischievious Polterpup. Surely he would find something in the mansion for you if you gave him one of it's favorite bones.\n__Get a random bit of loot from Polterpup. Single-use__")
        embed.add_field(name=f"2. Interdimensional dark-light device | {self.coin} 10", value="Ah my newest invention! Careful where you point it though deary.\n__Double your coins from the current ghost. Non-stackable. Single-use__")
        embed.add_field(name=f"3. Goo-igi stand-in | {self.coin} 15", value="It's almost like you can be in two places at once... oh wait silly me. You are!\n__2x total server dps for 1 hour. Stackable (to 5 max). Single-use__")
        embed.add_field(name=f"4. Magna-goo-tizer | {self.coin} 5", value="Ah I remember when I made this -- wait what did you say was for dinner again Mario?\n__Summons a ghost (if not already one); normal stat odds. Single-use__")
        embed.add_field(name=f"5. Chef Soulfflé's secret sauce | {self.coin} 20", value="I found it in a random coffee mug last weekend. Heard a scream when I took a sip. Probably nothing.\n__2x server coin drop for 2 hours. Non-stackable. Single-use__")
        embed.add_field(name=f"6. Magnificent banana | {self.coin} 15", value="I hear if someone slips on one of these bad boys they'll drop a bunch of coins...\n__Take a small bit of coins from all users during a fight. Single-use__")
        embed.add_field(name=f"7. Spirit ball | {self.coin} 30", value=f"A curious type of magic given by King Boo himself! I wonder what it could do for us?\n__Levels up your poltergust (.5x DPS, .1x coins compounding)\nAdditional 5 {self.coin} per level after first use__")
        embed.add_field(name=f"8. Strange title plaque | {self.coin} ???", value="I've always been curious what this little bugger was for, but I heard it's valuable! Although I'm sure there are some more... useful items in your journey you should try out first.\n__Exclusive event role on the server. Currently unavailable__")
        embed.add_field(name=f"?. Pretty flower | {self.coin} ?", value='I picked a bunch of these from the strange garden on the roof, but they went missing this morning. Maybe Polterpup just wanted to play with them...\n__???. Single-use__')

        await ctx.send(embed=embed)

    async def run_polterpup(self, user):
        self.ghost = True
        coin = [
            '<:coinreverse:638859919793324032>               <:coin:638882119682228236> <:coin:638882119682228236>   <:coinreverse:638859919793324032>    <:coin:638882119682228236>',
            '<:coinreverse:638859919793324032>               <:coin:638882119682228236> <:coin:638882119682228236>   <:coinreverse:638859919793324032>    <:coin:638882119682228236>  <:coinreverse:638859919793324032>',
            '<:coin:638882119682228236>                         <:coin:638882119682228236>   <:coinreverse:638859919793324032>    <:coin:638882119682228236>  <:coinreverse:638859919793324032>',
            '<:coin:638882119682228236><:coinreverse:638859919793324032>               <:coinreverse:638859919793324032>          <:coin:638882119682228236>         <:coinreverse:638859919793324032>',
            '<:coinreverse:638859919793324032>         <:coinreverse:638859919793324032>              <:coin:638882119682228236>            <:coinreverse:638859919793324032>',
            '<:coinreverse:638859919793324032>                   <:coin:638882119682228236>                <:coinreverse:638859919793324032>'
        ]
        random.shuffle(coin)
        header = f'Polterpup jumped out of **{user.mention}\'s** pretty flower  bouquet! It\'s dancing in the air and getting swirled with coins! React with {self.poltergustEmote} and get it down from there!\n\n'
        msg = await self.gameChannel.send(header + '\n'.join(coin), file=File('/root/mecha-bowser/events/resources/lm3/polterpup.png'))
        self.ghost = msg
        embed = Embed()
        embed.add_field(name='HP 999999999999...', value='[■■■■■■■■■■■■■■■■■■■■]■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■')
        embed.add_field(name='Time remaining', value='120 seconds')
        for x in range(120):
            dps = 0
            for attrs in self.participants.values():
                if not attrs['active']: continue
                userDps = attrs['dps'] * attrs['multiplier']
                dps += userDps

            embed.set_field_at(0, name=f'HP 999999999999... | {round(dps, 1)} DPS', value=f'[■■■■■■■■■■■■■■■■■■■■]■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■')
            embed.set_field_at(1, name='Time remaining', value=f'{120 - x} seconds')
            await asyncio.sleep(1)
            random.shuffle(coin)
            await msg.edit(content=header + '\n'.join(coin), embed=embed)

        chosenOnes = []
        for player, values in self.participants.items():
            if values['active']:
                chosenOnes.append(f'<@{player}>')
                mclient.bowser.lmEvent.update_one({'user': player}, {'$inc': {'coins': 100}})

        await msg.clear_reactions()
        await msg.edit(content=f'Polterpup, thinking that everyone was playing with it, ran away. Before you could chase after it though, all the coins dropped to the floor suddenly. ' + ', '.join(chosenOnes) + f' picked up 100 coins from the pile. Meanwhile {user.mention} is still holding the flowers, confused on what happened...\n\n' + '\n'.join(coin), file=None, embed=None, delete_after=60)
        self.ghost = None

    async def run_ghost(self, boss=False):
        self.ghost = True
        db = mclient.bowser.lmEvent
        attackGhost = random.choices(self.ghostTypes['ghosts'], [x['odds'] for x in self.ghostTypes['ghosts']])[0]
        if random.choices([True, False], weights=[5, 95])[0]:
            self.superSize = random.uniform(1.2, 2.6) # HP Multiplier

        content = f'{self.booEmote} A Ghost {random.choice(self.attackNames)}' if not self.superSize else f':rotating_light:{self.booEmote} A {random.choice(self.superSizedNames)} Ghost {random.choice(self.attackNames)}'
        content += f' Catch it by reacting with {self.poltergustEmote}!\nCheck your items and use them by opening your `!backpack`'
        embed = Embed()
        if self.superSize:
            embed.add_field(name='Time remaining', value='20 minutes')

        else:
            embed.add_field(name='Time remaining', value='10 minutes')

        maxhp = attackGhost['hp']
        hp = maxhp
        embed.add_field(name=f'HP {round(hp)}', value='[■■■■■■■■■■■■■■■■■■■■]')
        self.ghost = await self.gameChannel.send(content, file=File(attackGhost['image']), embed=embed)
        await self.ghost.add_reaction(self.poltergustEmote)
        value = attackGhost['value']
        if boss:
            expires = utils.resolve_duration('60m')

        elif self.superSize:
            expires = utils.resolve_duration('20m')
            maxhp *= self.superSize
            hp = maxhp
            value *= self.superSize

        else:
            expires = utils.resolve_duration('10m')

        editDelay = 1
        activeQte = None
        while hp > 0 and (expires - datetime.datetime.now()).total_seconds() > 0:
            editDelay -= 1
            await asyncio.sleep(1)
            dps = 0
            for attrs in self.participants.values():
                if not attrs['active']: continue
                userDps = (attrs['dps'] * attrs['multiplier']) * self.multiplier
                attrs['damage'] += userDps
                hp -= userDps
                dps += userDps

            if hp <= 0: break
            newBar = await self.health_bar(maxhp, hp)

            if editDelay <= 0:
                embed.set_field_at(1, name='Time remaining', value=utils.humanize_duration(expires))
                embed.set_field_at(0, name=f'HP {round(hp)} | {round(dps, 1)} DPS', value=f'[{newBar}]')
                await self.ghost.edit(embed=embed)
                editDelay = 1

            if random.choices([True, False], weights=[2, 98])[0]:
                if activeQte:
                    activeQte = random.choice(qte.qtes)
                    #await self.gameChannel.send()

        if hp > 0:
            await self.ghost.edit(content=f'{self.booEmote} The ghost got away, and no coins were recovered. Keep looking around for more ghosts!', embed=None, delete_after=60)

        else:
            msg = '' if not self.coinMultiplier else f'{self.coin} Coin multiplier is active {self.coinRvs}\n\n'
            bananaCoins = 0
            bananas = 0
            for x in self.activeItems:
                if x['id'] == 'banana' and x['active']: bananas += 1

            for user, attrs in self.participants.items():
                coins = round((attrs["damage"] / maxhp) * value)
                if 'double' in attrs['flags']:
                    coins *= 2

                if self.coinMultiplier:
                    coins *= 2

                if bananas:
                    bananaSub = 0.1 * bananas
                    coinsLost = round(bananaSub * coins)
                    bananaCoins += coinsLost
                    coins -= coinsLost

                if coins <= 0:
                    coins = 1
                msg += f'<@{user}> got {coins} coins\n' if not 'double' in attrs['flags'] else f'<@{user}> got {coins} coins (Earned double coins)\n'
                db.update_one({'user': user}, {'$inc': {'coins': coins, 'damage': attrs['damage']}})

            if bananas:
                bananaCoinPayout = math.ceil(bananaCoins / bananas)
                bananaMentions = []
                for x in self.activeItems[:]:
                    if x['id'] != 'banana' or not x['active']: continue
                    db.update_one({'user': x['user']}, {'$inc': {'coins': bananaCoinPayout}})

                    bananaMentions.append(f'<@{x["user"]}>')
                    self.activeItems.remove(x)
                    deactivatedBanana = x
                    deactivatedBanana['active'] = False
                    self.activeItems.append(deactivatedBanana)

                msg += '\n' + ', '.join(bananaMentions) + f' __made everyone else slip on **magnificent bananas** and took a total sum of {bananaCoinPayout} coins!__'

            await self.ghost.delete()
            await self.gameChannel.send(content=msg, embed=None, delete_after=60)

        self.ghost = None
        self.superSize = None
        self.participants = {}

    async def health_bar(self, maxhp, hp):
        chunks = round(maxhp / self.bars)
        emptyHp = int((maxhp - hp) / chunks)
        fullHp = self.bars - emptyHp
        return f"{'□' * emptyHp}{'■' * fullHp}"

    async def calculate_place(self, user=None):
        db = mclient.bowser.lmEvent.find({}).sort('damage', pymongo.DESCENDING)
        rankings = {}
        place = 0

        for x in db:
            if x['damage'] > 0:
                place += 1
                rankings[place] = {'user': x['user'], 'points': x['damage']}

        if user:
            for key, value in rankings.items():
                if value['user'] == user:
                    return key, value['points']

        else:
            return rankings

    @commands.command(name='leaderboard')
    async def _leaderboard(self, ctx):
        await ctx.message.delete()
        if ctx.channel.id not in [638872378545274900, 276036563866091521]:
            return await ctx.send(f'{ctx.author.mention} You can only use this command in <#638872378545274900>', delete_after=5)

        embed = Embed(title='Leaderboard', description='Current standings, sorted by total damage dealt overall:', color=0x4A90E2)
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

            points = f'__{round(value["points"])} dmg__' if value['points'] > 1 else '__1 damage__'
            embed.add_field(name=f'#{key}', value=f'{points} - {user}')

        if maxEntries == 10:
            # No scores yet
            embed.add_field(name='Scores', value='Hm, it looks like no one has a score yet. User scores will show up here after at least one person scores a point. Keep an eye out for geese!')

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name='backpack')
    async def _backpack(self, ctx):
        if ctx.channel.id != 638872378545274900:
            await ctx.message.delete()
            return await ctx.send(f'{ctx.author.mention} You can only use this command in <#638872378545274900>', delete_after=5)

        db = mclient.bowser.lmEvent
        user = db.find_one({'user': ctx.author.id})
        if not user:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any items! Play the game and catch some ghosts to spend some coins!', delete_after=10)

        items = {}
        for item, value in user['inventory'].items():
            item = int(item)
            if value > 0 and item not in items.keys():
                items[item] = value

        manifest = []
        for x in sorted(self.items.keys()):
            if x in items.keys():
                manifest.append(f'__{x}.__ {items[x]}x **' + self.items[x]['name'] + '**')

        if manifest:
            embed = Embed(title=f'Backpack | Level {user["level"]} |{self.coin} {user["coins"]}', description='Here are all of your stored items:\n\n' + '\n'.join(manifest))

        else:
            embed = Embed(title=f'Backpack | Level {user["level"]} |{self.coin} {user["coins"]}', description='You don\'t have any stored items. You can buy some in <#638872378545274900>')

        embed.add_field(name='Using items...', value='Each item has a number value listed next to it underlined, you can use this number to consume an item. A golden bone is ID 1 for example, so you can run `!use 1` to consume it')
        embed.set_thumbnail(url='https://cdn.discordapp.com/attachments/585528775471661244/638945309023797249/poltergust3000.png')
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)
        await ctx.message.delete()
        await ctx.send(embed=embed, delete_after=60)

    @commands.command(name='buy')
    async def _buy(self, ctx, item, quantity: typing.Optional[int] = 1):
        if ctx.channel.id != 638872378545274900:
            await ctx.message.delete()
            return await ctx.send(f'{ctx.author.mention} You can only use this command in <#638872378545274900>', delete_after=5)

        try:
            item = int(item)

        except (ValueError, TypeError):
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} That item doesn\'t exist!', delete_after=10)

        if item not in range(1, 8): # x + 1 to include max
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} That item doesn\'t exist!', delete_after=10)

        if item == 8 and 639156722086313984 in [x.id for x in ctx.author.roles]:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You already have the event role, you cannot buy another', delete_after=10)

        if quantity > 1 and item in [7, 8]:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot purchase more than one of **{self.items[item]["name"]}** at a time', delete_after=10)

        db = mclient.bowser.lmEvent
        user = db.find_one({'user': ctx.author.id})
        if not user:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any coins yet! Play the game and catch some ghosts to spend some coins!', delete_after=10)

        price = self.items[item]['price'] if item != 7 else (user['level'] * 5) + (user['inventory']['7'] * 5) + self.items[7]['price'] - 5
        if quantity:
            price *= quantity

        quant = 'a(n)' if not quantity else f'**{quantity}**'
        if user['coins'] < price:
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} Uh oh! You are {price - user["coins"]} coins short of being able to buy {quant} **{self.items[item]["name"]}**', delete_after=10)

        db.update_one({'user': ctx.author.id}, {'$set': {'coins': (user["coins"] - price)}, '$inc': {f'inventory.{item}': quantity}})
        await ctx.message.delete()
        return await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You\'ve purchased {quant} **{self.items[item]["name"]}** for {price} coins, and you have {user["coins"] - price} left now. Find and use your item by opening your `!backpack`', delete_after=10)

    @commands.command(name='use')
    async def _use(self, ctx, item, quantity: typing.Optional[int]):
        if ctx.channel.id not in [638872378545274900, 276036563866091521]:
            await ctx.message.delete()
            return await ctx.send(f'{ctx.author.mention} You can only use this command in <#638872378545274900>', delete_after=5)

        db = mclient.bowser.lmEvent
        user = db.find_one({'user': ctx.author.id})
        try:
            item = int(item)

        except (ValueError, TypeError):
            await ctx.message.delete()
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You are trying to use an invalid item', delete_after=10)

        if quantity and quantity > user['inventory'][str(item)]:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use more **{self.items[item]["name"]}** than you own!', delete_after=10)

        if quantity and item == 4:
            return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use more than 1 **{self.items[item]["name"]}** at a time!', delete_after=10)

        await ctx.message.delete()
        def remove_item(usedItem, check=False, count=1):
            usedItem = str(usedItem)
            if not user['inventory'][usedItem]: return False
            if user['inventory'][usedItem] < count:
                return False

            if not check: db.update_one({'user': ctx.author.id}, {'$inc': {f'inventory.{usedItem}': count * -1}})

            return True

        if not quantity: quantity = 1
        while quantity > 0:
            if item == 1:
                if not remove_item(item): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                gambleItems = [
                    {'type': 'item', 'value': 9, 'text': 'Polterpup came back with a pretty flower'},
                    {'type': 'negcoin', 'value': random.randint(-15, -5), 'text': 'Polterpup was so happy to have the bone, it buried it for safe keeping... and knocked you over in the process. Coins spill everywhere from your bag'},
                    {'type': 'poscoin', 'value': random.randint(3, 20), 'text': 'Polterpup was so thankful for the bone, it brought you a backpack it found! It\'s filled with coins!'},
                    {'type': 'trash', 'value': None, 'text': 'Polterpup took the bone and happily ran off into the mansion'},
                    {'type': 'item', 'value': 3, 'text': 'Polterpup ran into the other room to chew it\'s new bone, and came back with a mysterious goo!'},
                    {'type': 'negcoin', 'value': -25, 'text': 'Polterpup started jumping up and down around you, hitting the ground harder each time... harder... *harder* until the whole mansion shook! The floor gives out and you fall down to the basement'},
                    {'type': 'poscoin', 'value': 10, 'text': 'In Polterpup\'s excitement over the bone, it pukes all over you while wagging it\'s tail. Looks like it ate some coins!'},
                    {'type': 'poscoin', 'value': 5, 'text': 'After taking the bone excitedly, Polterpup nuzzles your leg... or more like goes through your leg. A few coins rain from the ceiling'},
                    {'type': 'trash', 'value': None, 'text': 'In thanks, Polterpup brings you a giant sack of coins! Unfortunately they are all ghost coins, and your hand goes right through them. You can\'t help but pat the doggo\'s head anyway though. Good doggo'},
                    {'type': 'negcoin', 'value': random.randint(-20, -5), 'text': 'Polterpup is so happy you got a golden bone for it, that it mistook the gold in your pocket for the bone and ran into the mansion!' },
                    {'type': 'trash', 'value': None, 'text': 'Polterpup is thrilled about the new bone! It calmly sits down on your foot and chews on it. Adorable'},
                    {'type': 'item', 'value': 2, 'text': 'When you showed Polterpup the bone you fell unconcious. This might have to do with the weird flashlight it had in it\'s mouth at that moment. When you came to all that was left was a *interdimentional dark-light device*'},
                    {'type': 'trash', 'value': None, 'text': 'Polterpup was really happy about the new golden bone! So much so it picked it up and put it in the pile of all the other... bones'},
                    {'type': 'poscoin', 'value': random.randint(5, 12), 'text': 'As soon as the bone left your pocket, a silly little dog with a sock on it\'s head trotted into the room. After taking off the sock, you found some coins inside!'}
                ]
                result = random.choice(gambleItems)
                msgStr = f'***{ctx.author.mention} pulled out a golden bone to give to Polterpup and...***\n{result["text"]}'
                if result['type'] == 'item':
                    db.update_one({'user': ctx.author.id}, {'$inc': {f'inventory.{result["value"]}': 1}})
                    msgStr += '\nGained 1x ' + self.items[result['value']]['name'] + ' to your backpack'

                elif result['type'] == 'negcoin':
                    if (user['coins'] + result['value']) < 0: # We don't want them going into the negative
                        subValue = result['value'] + user['coins']
                        msgStr += f'\nLost {abs(subValue)} coins. You now have **0**'
                        db.update_one({'user': ctx.author.id}, {'$set': {'coins': 0}})

                    else:
                        msgStr += f'\nLost {abs(result["value"])} coins. You now have {user["coins"] + result["value"]}'
                        db.update_one({'user': ctx.author.id}, {'$inc': {'coins': result['value']}})

                elif result['type'] == 'poscoin':
                    msgStr += f'\nGained {result["value"]} coins. You now have {user["coins"] + result["value"]}'
                    db.update_one({'user': ctx.author.id}, {'$inc': {'coins': result['value']}})

                await ctx.send(msgStr, delete_after=15)

            elif item == 2:
                if not remove_item(item, check=True): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                if not self.ghost or ctx.author.id not in self.participants.keys():
                    return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use the **{self.items[item]["name"]}** unless there is an active ghost and you are participating', delete_after=10)

                if 'double' in self.participants[ctx.author.id]['flags']:
                    return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot have more than one **{self.items[item]["name"]}** on an active ghost', delete_after=10)

                for x in self.activeItems:
                    if x['id'] == 'double' and x['user'] == ctx.author.id and x['active']:
                        return await ctx.send(f'{config.redTick} {ctx.author.mention} The **{self.items[item]["name"]}** is under a cooldown and can be used again in {utils.humanize_duration(x["expires"])}', delete_after=10)
    
                self.activeItems.append({'id': 'double', 'expires': utils.resolve_duration('1h'), 'user': ctx.author.id, 'active': True})
                self.participants[ctx.author.id]['flags'].append('double')
                remove_item(item)
                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. __Your__ coin output from the current ghost will be **doubled**', delete_after=10)

            elif item == 3:
                if not remove_item(item, check=True): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any ****. You can buy some in <#638872378545274900>', delete_after=10)
                if [x['id'] for x in self.activeItems].count('gooigi') >= 5:
                    firstExpires = []
                    for n in self.activeItems:
                        if n['id'] == 'gooigi':
                            firstExpires.append(n['expires'])

                    timeToFirstExpire = utils.humanize_duration(sorted(firstExpires)[0])
                    return await ctx.send(f'{config.redTick} {ctx.author.mention} The maximum amount of gooigi boosts is currently active. You can use this when the oldest one expires in {timeToFirstExpire}', delete_after=10)
    
                remove_item(item)
                self.activeItems.append({'id': 'gooigi', 'expires': utils.resolve_duration('1h')})
                if self.multiplier == 1:
                    self.multiplier = 2

                else:
                    self.multiplier += 1

                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. __The server__ now has a total DPS multiplier of **x{self.multiplier}**. Your boost will last for **one hour**', delete_after=10)

            elif item == 4:
                if not remove_item(item, check=True): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                if self.ghost: return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot summon a ghost when one already exists!')

                remove_item(item)
                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. A __ghost__ has been summoned in <#637351107999301633>!', delete_after=10)
                return await self.run_ghost() # This might be long running, but other command logic has passed so it should be okay

            elif item == 5:
                if not remove_item(item, check=True): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                for x in self.activeItems:
                    if x['id'] == 'sauce':
                        return await ctx.send(f'{config.redTick} {ctx.author.mention} Another **{self.items[item]["name"]}** is already active for the server. You can use this when it runs out in {utils.humanize_duration(x["expires"])}', delete_after=10)

                self.activeItems.append({'id': 'sauce', 'expires': utils.resolve_duration('2h')})
                self.coinMultiplier = True
                remove_item(item)
                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. __The server__ now has a **2x** coin boost for **2 hours**', delete_after=10)

            elif item == 6:
                if not remove_item(item, check=True): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                if not self.ghost or ctx.author.id not in self.participants.keys(): return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot use the **{self.items[item]["name"]}** unless there is an active ghost and you are participating', delete_after=10)
                for x in self.activeItems:
                    print(x)
                    if x['id'] == 'banana' and x['user'] == ctx.author.id and x['active']:
                        return await ctx.send(f'{config.redTick} {ctx.author.mention} You cannot have more than one **{self.items[item]["name"]}** active for yourself at one time. Please wait until the next ghost appears', delete_after=10)
        
                    if x['id'] == 'banana' and x['user'] == ctx.author.id and not x['active']:
                        return await ctx.send(f'{config.redTick} {ctx.author.mention} The **{self.items[item]["name"]}** is under a cooldown and can be used again in {utils.humanize_duration(x["expires"])}', delete_after=10)
    
                self.activeItems.append({'id': 'banana', 'expires': utils.resolve_duration('45m'), 'user': ctx.author.id, 'active': True})

                remove_item(item)
                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. You will steal a small amount of coins from each player who defeats the current ghost', delete_after=10)

            elif item == 7:
                if not remove_item(item): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                user = db.find_one_and_update({'user': ctx.author.id}, {'$inc': {'level': 1}})
                level = user['level']
                if self.ghost and ctx.author.id in self.participants.keys():
                    self.participants[ctx.author.id]['multiplier'] = 1 * (1.1**(level - 1))
                    self.participants[ctx.author.id]['level'] = level

                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. You have upgraded your poltergust to level **{level + 1}** and now have {(.5 * level)}x DPS | {round(.1 * level, 2)}x coin bonus', delete_after=10)

            elif item == 8:
                if not remove_item(item): return await ctx.send(f'{config.redTick} {ctx.author.mention} You don\'t have any **{self.items[item]["name"]}**. You can buy some in <#638872378545274900>', delete_after=10)
                await ctx.author.add_roles(ctx.guild.get_role(639156722086313984))
                await ctx.send(f'{config.greenTick} {ctx.author.mention} Success! You used your **{self.items[item]["name"]}**. It is now hung on your user profile for all to see', delete_after=10)

            elif item == 9:
                if not remove_item(item, count=5): return await ctx.send(f'{ctx.author.mention} The energy courses through you, but it is not enough. Not yet. You need more.', delete_after=10)
                if self.ghost: return await ctx.send(f'{config.redTick} {ctx.author.mention} While the flowers are powerful, you must defeat the correct ghost before using them', delete_after=10)
                await self.run_polterpup(ctx.author)

            quantity -= 1

def setup(bot):
    bot.add_cog(Mansion(bot))

def teardown(bot):
    bot.remove_cog('Mansion')