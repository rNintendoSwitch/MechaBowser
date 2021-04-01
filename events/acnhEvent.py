import asyncio
import logging
import random
import time
import typing

import config
import discord
import PIL
import pymongo
from discord.ext import commands, tasks


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)


class AnimalGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.eventRole = self.bot.get_guild(238080556708003851).get_role(761047949328646144)
        self.shopChannel = self.bot.get_channel(757411216774791189)
        self.leaderboard = self.bot.get_channel(769663694778400808)
        self.commandChannels = [
            769665679593832458,
            769665954241970186,
            769666021706432532,
        ]

        self.animals = {
            "Apollo": {
                "image": "https://cdn.mattbsg.xyz/rns/Apollo-01.png",
                "dialog1": "I feel like I just can’t stop doing the same things all the time. But I don’t care, PAH! So get me **{0}**.",
                "dialog2": "Oh my-don’t bother me! What? I’m supposed to tell you to get me something? Fine, whatever, get me **{0}**, pah!",
            },
            "Beau": {
                "image": "https://cdn.mattbsg.xyz/rns/Beau-01.png",
                "dialog1": "Hey... saltlick. Think you can get me **{0}**? I can’t get up, I have a brand new bag of spicy chips with my name on them.",
                "dialog2": "Listen, small favor, I could use **{0}**. Surely it would not be too much of a hassle, after all saltlick, you’re already moving!",
            },
            "Bill": {
                "image": "https://cdn.mattbsg.xyz/rns/Bill-01.png",
                "dialog1": "Yo! Still itching to do some fine handy work? That’s good! I hear you can find me **{0}**, so it would be great to work off those calories and get me them, quacko.",
                "dialog2": "Ah! I'm workin' up a sweat! Do you think you could get **{0}** for me? I need a break, quacko.",
            },
            "Bree": {
                "image": "https://cdn.mattbsg.xyz/rns/Bree-01.png",
                "dialog1": "Everyone keeps insisting I have more than enough to be happy. But I’m not happy at all! And if I’ve ever learned anything, it’s that money buys happiness! So get me **{0}**, cheeseball.",
                "dialog2": "Hey, cheeseball, heads up. I need **{0}**. I would get it myself but when you’re living my life you tend to be tired by the end of the minute.",
            },
            "Bunnie": {
                "image": "https://cdn.mattbsg.xyz/rns/Bunnie-01.png",
                "dialog1": "Hey, do you have **{0}**?  I've totally dreamed about getting one! Please let me know when you have it, tee-hee!",
                "dialog2": "You know what would go perfectly with my dress? A **{0}**, tee-hee! Can you please fetch me one? ",
            },
            "Chief": {
                "image": "https://cdn.mattbsg.xyz/rns/Chief-01.png",
                "dialog1": "Listen, I REALLY need **{0}**, harrrumph. I would probably get it myself, but I'm too busy right now. It would be a huge help if you could find it for me. So how is it looking?",
                "dialog2": "I can almost not believe I am asking you this, harrrumph. But I unfortunately need **{0}**, don’t let this get to your head!",
            },
            "Dobie": {
                "image": "https://cdn.mattbsg.xyz/rns/Dobie-01.png",
                "dialog1": "Hey, I've got a special favor to ask you... Here goes! I want **{0}**, ohmmm...",
                "dialog2": "Oh, they don’t make them like they used to. What are them, you ask? See for yourself and bring me **{0}**, ohmmm...",
            },
            "Freya": {
                "image": "https://cdn.mattbsg.xyz/rns/Freya-01.png",
                "dialog1": "I hate to ask you for a favor, but could you please get me **{0}**, uff da? ",
                "dialog2": "Will you lend me your hands and go bring over **{0}**, uff da?",
            },
            "Kyle": {
                "image": "https://cdn.mattbsg.xyz/rns/Kyle-01.png",
                "dialog1": "I’ve been busy making sure I look great before I go bug hunting right now so... could ya do me a solid, and get me **{0}**, alpha?",
                "dialog2": "Oh hey, you again... alpha! So i really need a **{0}** for a party coming up, could you go do your thing and get me it?",
            },
            "Lobo": {
                "image": "https://cdn.mattbsg.xyz/rns/Lobo-01.png",
                "dialog1": "Ah-roooo! It's not a good time! I just lost my **{0}**, it was my favorite! I want another one! Do you think you could get me one?",
                "dialog2": "Oh it's you. Well? Make yourself useful would ya? If anyone on this island could get me a **{0}** I am sure it’s you... so get on it... Ok? Ah-rooo!",
            },
            "Vesta": {
                "image": "https://cdn.mattbsg.xyz/rns/NH-Vesta-Render.png",
                "dialog1": "Baaaffo! Could you help me out, and get a **{0}** for me real quick? I know I can always count on you!",
                "dialog2": "Buddy! I heard you're looking for some jobs to do around the island, and I really need to get some stuff done... help a girl out and procure me **{0}** please? Baaaffo!",
            },
            "Octavian": {
                "image": "https://cdn.mattbsg.xyz/rns/Octavian-01.png",
                "dialog1": "Heya, Sucker, you got a **{0}** I can have? It'd really help me out if you did!",
                "dialog2": "Hey sucker! You’re sure running around a lot today, rather than making me dizzy, could ya make yourself useful and magic up a **{0}**?",
            },
            "Skye": {
                "image": "https://cdn.mattbsg.xyz/rns/Skye-01.png",
                "dialog1": "Ah... I have been running low on stuff for a while. I hope you can get me **{0}**, airmail.",
                "dialog2": "Just a moment ago, I saw a cloud that really reminded me of **{0}**, airplane. If you find one, could you please bring it over?",
            },
            "Tank": {
                "image": "https://cdn.mattbsg.xyz/rns/Tank-01.png",
                "dialog1": "Yoo, I've been training so hard I forgot I needed a **{0}** for my cool down... would you mind getting me some while I finish my last lap? kerPOW!",
                "dialog2": "Wassup! I just finished my jog, and I was wondering, any ideas where i could get a **{0}**, if you can, hit me up, you know where I'll be, kerPOW!",
            },
            "Vivian": {
                "image": "https://cdn.mattbsg.xyz/rns/Vivian-01.png",
                "dialog1": "Oh hey! Could you do me, the SOON TO BE ULTRA FAMOUS Vivian a favor? Go and fetch me a **{0}**, piffle!",
                "dialog2": "Sooo, you prefer stuffing your face to taking a nap, eh? You know what, would you mind making yourself useful and bring me **{0}**, piffle?",
            },
            "Whitney": {
                "image": "https://cdn.mattbsg.xyz/rns/Whitney-01.png",
                "dialog1": "Oh, you're truly priceless, you know that? Would you mind bringing me a **{0}**, snappy?",
                "dialog2": "I hate to admit it but you have some skill. So your my only beacon of hope to get **{0}**, snappy.",
            },
            "Zucker": {
                "image": "https://cdn.mattbsg.xyz/rns/Zucker-01.png",
                "dialog1": "Hey! Bloop! The bugs tell me I really need to get a **{0}** right now, could you help a fella out?",
                "dialog2": "Haven't seen you in at least a second! Umm I got a favor to ask, bloop, I heard you're the person to ask to get hold of **{0}**, could you hook me up?",
            },
        }
        self.fruits = {
            'apple': 'https://cdn.mattbsg.xyz/rns/apple.png',
            'orange': 'https://cdn.mattbsg.xyz/rns/orange.png',
            'peach': 'https://cdn.mattbsg.xyz/rns/peach.png',
            'pear': 'https://cdn.mattbsg.xyz/rns/pear.png',
            'cherry': 'https://cdn.mattbsg.xyz/rns/cherries.png',
            'turnip': 'https://cdn.mattbsg.xyz/rns/turnip.png',
        }
        self.fish = {
            "black-bass": {
                "name": "Black Bass",
                "image": "https://cdn.mattbsg.xyz/rns/black-bass.png",
                "value": 100,
                "weight": 4,
                "pun": "Turn up the beat!",
            },
            "carp": {
                "name": "Carp",
                "image": "https://cdn.mattbsg.xyz/rns/carp.png",
                "value": 50,
                "weight": 5,
                "pun": "Sir, this is the HOV lane.",
            },
            "crucian-carp": {
                "name": "Crucian Carp",
                "image": "https://cdn.mattbsg.xyz/rns/crucian-carp.png",
                "value": 50,
                "weight": 5,
                "pun": "At least it's not a crustacean.",
            },
            "dab": {
                "name": "Dab",
                "image": "https://cdn.mattbsg.xyz/rns/dab.png",
                "value": 75,
                "weight": 5,
                "pun": "Dabbing since 2014.",
            },
            "freshwater-goby": {
                "name": "Freshwater Goby",
                "image": "https://cdn.mattbsg.xyz/rns/freshwater-goby.png",
                "value": 150,
                "weight": 4,
                "pun": "Gotta go-by some more bait.",
            },
            "loach": {
                "name": "Loach",
                "image": "https://cdn.mattbsg.xyz/rns/loach.png",
                "value": 100,
                "weight": 4,
                "pun": "Stop! Do not approach!",
            },
            "ocean-sunfish": {
                "name": "Ocean Sunfish",
                "image": "https://cdn.mattbsg.xyz/rns/ocean-sunfish.png",
                "value": 200,
                "weight": 3,
                "pun": "Maybe tonight I'll find an ocean moon-fish!",
            },
            "olive-flounder": {
                "name": "Olive Flounder",
                "image": "https://cdn.mattbsg.xyz/rns/olive-flounder.png",
                "value": 150,
                "weight": 4,
                "pun": "I found 'er",
            },
            "red-snapper": {
                "name": "Red Snapper",
                "image": "https://cdn.mattbsg.xyz/rns/red-snapper.png",
                "value": 700,
                "weight": 2,
                "pun": "You've been caught red handed!",
            },
            "sea-bass": {
                "name": "Sea Bass",
                "image": "https://cdn.mattbsg.xyz/rns/sea-bass.png",
                "value": 150,
                "weight": 4,
                "pun": "Best music unda da sea.",
            },
            "sea-butterfly": {
                "name": "Sea Butterfly",
                "image": "https://cdn.mattbsg.xyz/rns/sea-butterfly.png",
                "value": 300,
                "weight": 3,
                "pun": "Wait, can this thing fly?",
            },
            "shark": {
                "name": "Shark",
                "image": "https://cdn.mattbsg.xyz/rns/shark.png",
                "value": 1500,
                "weight": 1,
                "pun": "I finn-a-ly caught one!",
            },
            "squid": {
                "name": "Squid",
                "image": "https://cdn.mattbsg.xyz/rns/squid.png",
                "value": 175,
                "weight": 4,
                "pun": "Ink-sightful!",
            },
            "tadpole": {
                "name": "Tadpole",
                "image": "https://cdn.mattbsg.xyz/rns/tadpole.png",
                "value": 25,
                "weight": 5,
                "pun": "I have a few questions. Can you answer my tad poll?",
            },
            "yellow-perch": {
                "name": "Yellow Perch",
                "image": "https://cdn.mattbsg.xyz/rns/yellow-perch.png",
                "value": 50,
                "weight": 5,
                "pun": "Take a seat if you are feeling mellow.",
            },
        }
        self.bugs = {
            "butterfly": {
                "name": "Butterfly",
                "image": "https://cdn.mattbsg.xyz/rns/common-butterfly.png",
                "value": 100,
                "weight": 5,
                "pun": "It better fly.",
            },
            "hermit-crab": {
                "name": "Hermit Crab",
                "image": "https://cdn.mattbsg.xyz/rns/hermit-crab.png",
                "value": 300,
                "weight": 3,
                "pun": "The laziest type of crab.",
            },
            "moth": {
                "name": "Moth",
                "image": "https://cdn.mattbsg.xyz/rns/moth.png",
                "value": 100,
                "weight": 5,
                "pun": "Let there be light!",
            },
            "pill-bug": {
                "name": "Pill Bug",
                "image": "https://cdn.mattbsg.xyz/rns/pill-bug.png",
                "value": 100,
                "weight": 5,
                "pun": "Ever hear of a rollie polly?",
            },
            "spider": {
                "name": "Spider",
                "image": "https://cdn.mattbsg.xyz/rns/spider.png",
                "value": 200,
                "weight": 4,
                "pun": "Eight legs too many.",
            },
            "tarantula": {
                "name": "Tarantula",
                "image": "https://cdn.mattbsg.xyz/rns/tarantula.png",
                "value": 900,
                "weight": 1,
                "pun": "Almost as hairy as my uncle (who works at Nintendo).",
            },
            "wharf-roach": {
                "name": "Wharf Roach",
                "image": "https://cdn.mattbsg.xyz/rns/wharf-roach.png",
                "value": 100,
                "weight": 5,
                "pun": "Not as scary as your coach.",
            },
        }
        self.items = {
            "bait": {
                "name": "Bait",
                "image": "https://cdn.mattbsg.xyz/rns/bait.png",
                "value": 100,
            },
            "bells": {
                "name": "Bells",
                "image": "https://cdn.mattbsg.xyz/rns/bells.png",
                "value": 0,
            },
            "stick": {
                "name": "Stick",
                "image": "https://cdn.mattbsg.xyz/rns/crafting-item-1.png",
                "value": 25,
            },
            "iron-nugget": {
                "name": "Iron Nugget",
                "image": "https://cdn.mattbsg.xyz/rns/crafting-item-2.png",
                "value": 500,
            },
            "clay": {
                "name": "Clay",
                "image": "https://cdn.mattbsg.xyz/rns/crafting-item-5.png",
                "value": 50,
            },
            "stone": {
                "name": "Stone",
                "image": "https://cdn.mattbsg.xyz/rns/crafting-item-6.png",
                "value": 25,
            },
            "diy": {
                "name": "DIY Recipe",
                "image": "https://cdn.mattbsg.xyz/rns/diy.png",
                "value": 0,
            },
            "furniture": {
                "name": "Furniture",
                "image": "https://cdn.mattbsg.xyz/rns/furniture.png",
                "value": 0,
            },
            "hat": {
                "name": "Hat",
                "image": "https://cdn.mattbsg.xyz/rns/hat.png",
                "value": 0,
            },
            "present": {
                "name": "Present",
                "image": "https://cdn.mattbsg.xyz/rns/present.png",
                "value": 0,
            },
            "shell": {
                "name": "Shell",
                "image": "https://cdn.mattbsg.xyz/rns/shell.png",
                "value": 20,
            },
            "conch": {
                "name": "Conch",
                "image": "https://cdn.mattbsg.xyz/rns/conch.png",
                "value": 30,
            },
            "cowrie": {
                "name": "Cowrie",
                "image": "https://cdn.mattbsg.xyz/rns/cowrie.png",
                "value": 25,
            },
            "coral": {
                "name": "Coral",
                "image": "https://cdn.mattbsg.xyz/rns/coral.png",
                "value": 250,
            },
            "sand-dollar": {
                "name": "Sand Dollar",
                "image": "https://cdn.mattbsg.xyz/rns/sand-dollar.png",
                "value": 10,
            },
            "tree": {
                "name": "Tree",
                "image": "https://cdn.mattbsg.xyz/rns/tree-2b.png",
                "value": 0,
            },
        }
        self.rarity = {
            1: "ultra rare",
            2: "rare",
            3: "somewhat rare",
            4: "uncommon",
            5: "common",
        }
        self.activeBait = {}
        self.completedQuests = {}
        self.actionLock = []
        self.travelers = {}  # id: {expiry: time, host: id, hitQuota: False}

        db = mclient.bowser.animalEvent
        doc = db.find_one({"_type": "server"})
        self.durabilities = {int(x): y for x, y in doc["durabilities"].items()}
        self.completedQuests = {int(x): y for x, y in doc["completedQuests"].items()}
        self.todaysQuests = doc["quests"]
        # self._roll_quests()
        for user in db.find({"_type": "user"}):
            if user["_id"] not in self.durabilities.keys():
                self.durabilities[user["_id"]] = {
                    "fishrod": {"value": 25, "regenAt": None},
                    "shovel": {"value": 20, "regenAt": None},
                    "bait": {"value": 1, "regenAt": None},
                    "gift": {"value": 3, "regenAt": None},
                }

        self._regen_tools.start()  # pylint: disable=no-member
        self._leaderboard_update.start()  # pylint: disable=no-member

    def cog_unload(self):
        db = mclient.bowser.animalEvent
        newDura = {str(x): y for x, y in self.durabilities.items()}
        newCom = {str(x): y for x, y in self.completedQuests.items()}
        db.update_one(
            {"_id": "server"},
            {
                "$set": {
                    "quests": self.todaysQuests,
                    "durabilities": newDura,
                    "completedQuests": newCom,
                }
            },
        )
        self._regen_tools.cancel()  # pylint: disable=no-member
        self._leaderboard_update.cancel()  # pylint: disable=no-member

    def _roll_quests(self):
        """
        Helper function that randomly rolls quest information
        for all villagers

        return: Dict of villager keys, values of 1) text phrase, 2) item request data
        """
        questItems = (
            list(self.fish.keys())
            + list(self.bugs.keys())
            + list(self.fruits.keys())
            + [
                "bait",
                "stick",
                "iron-nugget",
                "clay",
                "stone",
                "shell",
                "conch",
                "cowrie",
                "coral",
                "sand-dollar",
            ]
        )
        for animal, data in self.animals.items():
            dialogChoices = [data["dialog1"], data["dialog2"]]
            requestedItem = random.choice(questItems)
            if requestedItem in self.fish.keys():
                itemName = self.fish[requestedItem]["name"].lower()
                itemImage = self.fish[requestedItem]["image"]
                itemCost = self.fish[requestedItem]["value"] * 0.2 + self.fish[requestedItem]["value"]
                catID = "fish"

            elif requestedItem in self.bugs.keys():
                itemName = self.bugs[requestedItem]["name"].lower()
                itemImage = self.bugs[requestedItem]["image"]
                itemCost = self.bugs[requestedItem]["value"] * 0.2 + self.bugs[requestedItem]["value"]
                catID = "bugs"

            elif requestedItem in self.fruits.keys():
                itemName = requestedItem
                itemImage = self.fruits[requestedItem]
                itemCost = 1400 if itemName == 'turnip' else 800
                catID = 'fruit'

            else:
                itemName = self.items[requestedItem]["name"].lower()
                itemImage = self.items[requestedItem]["image"]
                itemCost = self.items[requestedItem]["value"] * 0.2 + self.items[requestedItem]["value"]
                catID = "items"

            self.todaysQuests[animal] = {
                "text": random.choice(dialogChoices).format(itemName),
                "item": requestedItem,
                "itemName": itemName,
                "catID": catID,
                "value": random.randint(1, 4),
                "itemCost": itemCost,
                "image": itemImage,
            }

    @tasks.loop(minutes=5)
    async def _leaderboard_update(self):
        db = mclient.bowser.animalEvent
        users = db.find({"_type": "user"}).sort([("bells", -1)])
        desc = ""
        for x in range(1, 26):
            try:
                user = users[x - 1]
                desc += "**#{}** - {:,} bells <@{}>\n".format(x, user["bells"], user["_id"])

            except IndexError:
                break

        if desc:
            desc = "The current event standings for players with the most amount of bells are:\n\n" + desc

        embed = discord.Embed(
            title="Event Leaderboard",
            color=0x83D632,
            description=desc or "There are not enough players yet to display rankings, go play and have fun!",
        )

        embed.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/276036563866091521/697431380103266354/unknown.png"
        )
        embed.set_author(
            name="/r/NintendoSwitch",
            icon_url="https://cdn.discordapp.com/attachments/276036563866091521/698093488910237757/snoo.png",
        )
        embed.set_footer(text="These standings are updated frequently")
        async for message in self.leaderboard.history(limit=1):
            return await message.edit(embed=embed)

        await self.leaderboard.send(embed=embed)

    @tasks.loop(seconds=30)
    async def _regen_tools(self):
        logging.debug("[ACEvent] Running regen tools")
        localDurabilities = self.durabilities.copy()
        for user, tools in localDurabilities.items():
            if tools["fishrod"]["regenAt"] and tools["fishrod"]["regenAt"] < time.time():
                localDurabilities[user]["fishrod"]["regenAt"] = None
                localDurabilities[user]["fishrod"]["value"] = 25

            if tools["shovel"]["regenAt"] and tools["shovel"]["regenAt"] < time.time():
                localDurabilities[user]["shovel"]["regenAt"] = None
                localDurabilities[user]["shovel"]["value"] = 20

            if tools["bait"]["regenAt"] and tools["bait"]["regenAt"] < time.time():
                localDurabilities[user]["bait"]["regenAt"] = None
                localDurabilities[user]["bait"]["value"] = 1

            if tools["gift"]["regenAt"] and tools["gift"]["regenAt"] < time.time():
                localDurabilities[user]["gift"]["regenAt"] = None
                localDurabilities[user]["gift"]["value"] = 3

        localActiveBait = self.activeBait.copy()
        for user, expiry in localActiveBait.items():
            if expiry < time.time():
                del self.activeBait[user]

    # @tasks.loop(hours=24)
    @commands.is_owner()
    @commands.command(name="reset")
    async def _daily_reset(self, ctx):
        db = mclient.bowser.animalEvent

        # Forget durability usage and restore tools
        self.durabilities = {}
        self.completedQuests = {}

        for user in db.find({"_type": "user"}):
            self.durabilities[user["_id"]] = {
                "fishrod": {"value": 25, "regenAt": None},
                "shovel": {"value": 20, "regenAt": None},
                "bait": {"value": 1, "regenAt": None},
                "gift": {"value": 3, "regenAt": None},
            }

            # Advance saplings and regrow fruit
            newTrees = {}
            availableFruit = {}
            runTrees = False
            for treeType, saplings in user["saplings"].items():
                newTrees[treeType] = saplings
                if saplings:
                    runTrees = True

            for treeType, trees in user["trees"].items():
                availableFruit[treeType] = trees * 3

            unpickedFruit = {"unpickedFruit." + x: availableFruit[x] for x in availableFruit.keys()}
            unpickedFruit["saplings"] = {}
            db.update_one({"_id": user["_id"]}, {"$set": unpickedFruit})
            if runTrees:
                db.update_one(
                    {"_id": user["_id"]},
                    {"$inc": {"trees." + x: newTrees[x] for x in newTrees.keys()}},
                )

        # Reset quests
        self._roll_quests()

    @commands.is_owner()
    @commands.command(name="savequests")
    async def _save_quests(self, ctx):
        db = mclient.bowser.animalEvent
        db.update_one({"_id": "server"}, {"$set": {"quests": self.todaysQuests}})
        await ctx.send("Saved quests to db")

    @commands.is_owner()
    @commands.command(name="restorequests")
    async def _restore_quests(self, ctx):
        db = mclient.bowser.animalEvent
        doc = db.find_one({"_id": "server"})
        self.todaysQuests = doc["quests"]
        await ctx.send("Restored quests from db")

    @commands.is_owner()
    @commands.command(name="pricepost")
    async def _pricepost(self, ctx):
        await ctx.message.delete()
        textPost = f"Hello!\nAre you looking to sell something? You\'ve came to the right place! Use the `!sell amt item` command, replacing \"amt\" with the number of items to sell and \"item\" with the item you want to sell. If you just want to sell 1 item, you can just use the `!sell item` command. To sell all items of a specific category, use `!sell category` replacing \"category\" with the category you want to sell.\nOur prices may change, so please be sure to check back every day!\n\n__Fruit:__\nNative fruit is fruit from your island, while foreign fruit is from other people\'s islands\n\n**Native Fruit** - 400 Bells\n**Foreign Fruit** - 600 Bells\n**Turnip** - 1000 Bells\n\n__Fish:__\n\n**Black Bass** - {self.fish['black-bass']['value']} Bells\n**Carp** - {self.fish['carp']['value']} Bells\n**Crucian Carp** - {self.fish['crucian-carp']['value']} Bells\n**Dab** - {self.fish['dab']['value']} Bells\n**Freshwater Goby** - {self.fish['freshwater-goby']['value']} Bells\n**Loach** - {self.fish['loach']['value']} Bells\n**Ocean Sunfish** - {self.fish['ocean-sunfish']['value']} Bells\n**Olive Flounder** - {self.fish['olive-flounder']['value']} Bells\n**Red Snapper** - {self.fish['red-snapper']['value']} Bells\n**Sea Bass** - {self.fish['sea-bass']['value']} Bells\n**Sea Butterfly** - {self.fish['sea-butterfly']['value']} Bells\n**Shark** - {self.fish['shark']['value']} Bells\n**Squid** - {self.fish['squid']['value']} Bells\n**Tadpole** - {self.fish['tadpole']['value']} Bells\n**Yellow Perch** - {self.fish['yellow-perch']['value']} Bells\n\n__Bugs:__\n\n**Butterfly** - {self.bugs['butterfly']['value']} Bells\n**Hermit Crab** - {self.bugs['hermit-crab']['value']} Bells\n**Moth** - {self.bugs['moth']['value']} Bells\n**Pill Bug** - {self.bugs['pill-bug']['value']} Bells\n**Spider** - {self.bugs['spider']['value']} Bells\n**Tarantula** - {self.bugs['tarantula']['value']} Bells\n**Wharf Roach** - {self.bugs['wharf-roach']['value']} Bells\n\n__Misc:__\n\n**Bait** - {self.items['bait']['value']} Bells\n**Clay** - {self.items['clay']['value']} Bells\n**Conch** - {self.items['conch']['value']} Bells\n**Coral** - {self.items['coral']['value']} Bells\n**Cowrie** - {self.items['cowrie']['value']} Bells\n**Iron Nugget** - {self.items['iron-nugget']['value']} Bells\n**Sand Dollar** - {self.items['sand-dollar']['value']} Bells\n**Shell** - {self.items['shell']['value']} Bells\n**Stick** - {self.items['stick']['value']} Bells\n**Stone** - {self.items['stone']['value']} Bells"
        embed = discord.Embed(title='Nooks Cranny', color=0xFFF62D, description=textPost)
        embed = discord.Embed(title="Nooks Cranny", color=0xFFF62D, description=textPost)
        embed.set_thumbnail(url="https://cdn.mattbsg.xyz/rns/Timmy-Tommy-01.png")
        await self.shopChannel.send(embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="pay")
    async def _pay(self, ctx, amount: int):
        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        await ctx.message.delete()
        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        if user["finished"]:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You don't owe Nook Inc. any bells! Did you mean to visit Blathers with `!donate`?",
                delete_after=10,
            )

        if amount <= 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You must provide a number greater than or equal to 1",
                delete_after=10,
            )

        if amount > user["bells"]:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You do not have enough bells to cover that amount!",
                delete_after=10,
            )

        if amount >= user["debt"]:
            amount = user["debt"]
            db.update_one(
                {"_id": ctx.author.id},
                {"$inc": {"bells": -1 * amount}, "$set": {"debt": 0, "finished": True}},
            )
            mclient.bowser.users.update_one({"_id": ctx.author.id}, {"$push": {"backgrounds": "animalcrossing"}})
            return await ctx.send(
                f"🎉 Success! You made a payment of **{amount}** bells towards your loan and paid it off in full! Woop! You got the **Animal Crossing: New Horizons profile background** -- to equip it use `!profile edit` 🎉\nAdditionally, you now have access to the `!donate` command, why not try it out?"
            )

        db.update_one(
            {"_id": ctx.author.id},
            {"$inc": {"bells": -1 * amount, "debt": -1 * amount}},
        )
        return await ctx.send(f"Success! You made a payment of **{amount}** bells towards your loan!")

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="donate")
    async def _donate(self, ctx, *, item: typing.Optional[str] = ""):
        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        await ctx.message.delete()

        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        if not user["finished"]:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Thanks for stopping by! It looks like you have some outstanding debt, why not come back after you are all set?",
                delete_after=10,
            )

        if user["townhall"] == 0:
            db.update_one({"_id": ctx.author.id}, {"$set": {"debt": 200000, "townhall": 1}})
            return await ctx.send(
                f"{ctx.author.mention} Thanks for stopping by! So Tom Nook sent you? Great! I could some use some capital to help build the island museum.\nSpecifically, I need **200,000** bells to construct the new building -- come back and see me with `!donate` after you've got it!"
            )

        if user["townhall"] == 1 and user["bells"] < 200000:
            return await ctx.send(
                f"{ctx.author.mention} Thanks for stopping by! Thanks again for helping out with building the museum, come back and see me with `!donate` when you have the **200,000** bells on hand!"
            )

        if user["townhall"] == 1 and user["bells"] >= 200000:
            bellsOwed = 0
            for fish, value in user["fish"].items():
                if value <= 0:
                    continue
                bellsOwed += self.fish[fish]["value"] * value

            for bug, value in user["bugs"].items():
                if value <= 0:
                    continue
                bellsOwed += self.bugs[bug]["value"] * value

            for fruit, value in user["fruit"].items():
                if value <= 0:
                    continue
                if fruit == user["homeFruit"]:
                    bellsOwed += 400 * value

                else:
                    bellsOwed += 600 * value

            for misc, value in user["items"].items():
                if value <= 0:
                    continue
                bellsOwed += self.items[misc]["value"] * value

            db.update_one(
                {"_id": ctx.author.id},
                {
                    "$set": {
                        "fish": {},
                        "bugs": {},
                        "fruit": {},
                        "items": {},
                        "debt": 0,
                        "bells": (user["bells"] + bellsOwed) - 200000,
                    },
                    "$inc": {"lifetimeBells": bellsOwed, "townhall": 1},
                },
            )
            return await ctx.send(
                f"{ctx.author.mention} Thanks for stopping by! Awesome, you have the bells and items we need for the project! Oh, the items? I've gone ahead and emptied your backback out since we could use anything on hand! Timmy and Tommy will compensate you **{bellsOwed}** bells for the trouble. Go out and catch **two of every fish and bug** for the museum! When you've got something stop by and `!donate` it."
            )

        saniItem = item.lower().strip().replace(" ", "-")
        print(saniItem)
        if user["townhall"] == 2 and saniItem:
            if user["museum"].count(saniItem) >= 2:
                return await ctx.send(
                    f"{ctx.author.mention} Why thanks for bringing by **{item.lower()}**, but we do not need any for our collection! If you need to know what we still need checkout `!donate`"
                )

            if saniItem not in list(self.fish.keys()) + list(self.bugs.keys()):
                return await ctx.send(
                    f"{ctx.author.mention} Why thanks for bringing by **{item.lower()}**, but we do not need any for our collection! If you need to know what we still need checkout `!donate`"
                )

            if saniItem in user["fish"].keys() and user["fish"][saniItem] >= 1:
                db.update_one(
                    {"_id": ctx.author.id},
                    {"$inc": {"fish." + saniItem: -1}, "$push": {"museum": saniItem}},
                )
                return await ctx.send(
                    f"{ctx.author.mention} Why thanks for bringing by a **{item.lower()}**! I can take that wonderful sea faring creature off your hands for our collection at once!"
                )

            elif saniItem in user["bugs"].keys() and user["bugs"][saniItem] >= 1:
                db.update_one(
                    {"_id": ctx.author.id},
                    {"$inc": {"bugs." + saniItem: -1}, "$push": {"museum": saniItem}},
                )
                return await ctx.send(
                    f"{ctx.author.mention} Why thanks for bringing by a **{item.lower()}**! I can take that wretched creature off your hands for our collection at once!"
                )

            else:
                return await ctx.send(
                    f"{ctx.author.mention} Oh deary, it looks like you have no **{item.lower()}** that I can take! Why not drop by after a bit once you've got one?"
                )

        if user["townhall"] == 2 and not saniItem:
            embed = discord.Embed(title="Island Museum", color=0x194499)
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
            embed.set_thumbnail(url="https://cdn.mattbsg.xyz/rns/Blathers-01.png")
            description = 'Heyo, hoot hoot! Thanks for swinging by, here is our collection and a list of creatures we still need! You can donate an item by using `!donate item` replacing "item" with the name of the creature!\n\n__Fish__\n'
            missingItem = 0
            for fish in self.fish.keys():
                if fish not in user["museum"]:
                    description += "･ " + self.fish[fish]["name"] + " [0/2]\n"
                    missingItem += 1
                    continue

                if user["museum"].count(fish) >= 2:
                    description += "･ " + self.fish[fish]["name"] + f" **[COMPLETE]**\n"

                else:
                    missingItem += 1
                    description += "･ " + self.fish[fish]["name"] + f' [{user["museum"].count(fish)}/2]\n'

            description += "\n\n__Bugs__\n"

            for bug in self.bugs.keys():
                if bug not in user["museum"]:
                    description += "･ " + self.bugs[bug]["name"] + " [0/2]\n"
                    missingItem += 1
                    continue

                elif user["museum"].count(bug) >= 2:
                    description += "･ " + self.bugs[bug]["name"] + f" **[COMPLETE]**\n"

                else:
                    missingItem += 1
                    description += "･ " + self.bugs[bug]["name"] + f' [{user["museum"].count(bug)}/2]\n'

            if missingItem == 0:
                description = "Heyo, hoot hoot! Thanks for swinging by, here is our collection! We are very thankful that **you've donated two of every creature on the island**.\n:tada: I couldn't have done it without your help!\n\n__Fish__\n"
                for data in self.fish.values():
                    description += "･ " + data["name"] + "\n"

                description += "\n\n__Bugs__\n"

                for data in self.bugs.values():
                    description += "･ " + data["name"] + "\n"

            embed.description = description
            await ctx.send(ctx.author.mention, embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="sell")
    async def _sell(self, ctx, quantity: typing.Optional[int] = 1, *, item):
        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        if ctx.channel.id != 757411216774791189:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can only use this command in {self.shopChannel.mention}!",
                delete_after=10,
            )

        await ctx.message.delete()
        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        saniItem = item.lower().strip().replace(" ", "-")

        if quantity <= 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can sell at a minimum 1 item, not {quantity}",
                delete_after=10,
            )

        itemCnt = 0
        bellsOwed = 0
        if saniItem == "fish":  # Sell ALL fish
            for fish, value in user[saniItem].items():
                if value <= 0:
                    continue
                itemCnt += 1
                bellsOwed += self.fish[fish]["value"] * value

            if not itemCnt:
                return await ctx.send(
                    f"{config.redTick} {ctx.author.mention} You don't have any fish to sell!",
                    delete_after=10,
                )

            db.update_one(
                {"_id": ctx.author.id},
                {
                    "$set": {"fish": {}},
                    "$inc": {"bells": bellsOwed, "lifetimeBells": bellsOwed},
                },
            )
            return await ctx.send(
                f"{ctx.author.mention} Success! You sold all your fish items for a total of **{bellsOwed}** bells!",
                delete_after=10,
            )

        elif saniItem == "bugs":  # Sell ALL bugs
            for bug, value in user[saniItem].items():
                if value <= 0:
                    continue
                itemCnt += 1
                bellsOwed += self.bugs[bug]["value"] * value

            if not itemCnt:
                return await ctx.send(
                    f"{config.redTick} {ctx.author.mention} You don't have any bugs to sell!",
                    delete_after=10,
                )

            db.update_one(
                {"_id": ctx.author.id},
                {
                    "$set": {"bugs": {}},
                    "$inc": {"bells": bellsOwed, "lifetimeBells": bellsOwed},
                },
            )
            return await ctx.send(
                f"{ctx.author.mention} Success! You sold all your bug items for a total of **{bellsOwed}** bells!",
                delete_after=10,
            )

        elif saniItem == "fruit":  # Sell ALL fruit
            for fruit, value in user[saniItem].items():
                if value <= 0:
                    continue
                itemCnt += 1
                if fruit == user["homeFruit"]:
                    bellsOwed += 400 * value

                elif fruit == 'turnip':
                    bellsOwed += 1000 * value

                else:
                    bellsOwed += 600 * value

            if not itemCnt:
                return await ctx.send(
                    f"{config.redTick} {ctx.author.mention} You don't have any fruit to sell!",
                    delete_after=10,
                )

            db.update_one(
                {"_id": ctx.author.id},
                {
                    "$set": {"fruit": {}},
                    "$inc": {"bells": bellsOwed, "lifetimeBells": bellsOwed},
                },
            )
            return await ctx.send(
                f"{ctx.author.mention} Success! You sold all your fruit items for a total of **{bellsOwed}** bells!",
                delete_after=10,
            )

        elif saniItem == "misc":  # Sell ALL misc items
            for misc, value in user["items"].items():
                if value <= 0:
                    continue
                itemCnt += 1
                bellsOwed += self.items[misc]["value"] * value

            if not itemCnt:
                return await ctx.send(
                    f"{config.redTick} {ctx.author.mention} You don't have any misc items to sell!",
                    delete_after=10,
                )

            db.update_one(
                {"_id": ctx.author.id},
                {
                    "$set": {"items": {}},
                    "$inc": {"bells": bellsOwed, "lifetimeBells": bellsOwed},
                },
            )
            return await ctx.send(
                f"{ctx.author.mention} Success! You sold all your misc items for a total of **{bellsOwed}** bells!",
                delete_after=10,
            )

        else:
            for name, value in user["fish"].items():
                if not value:
                    continue
                if name == saniItem:
                    sellAmt = value if quantity > value else quantity
                    bellsOwed = sellAmt * self.fish[saniItem]["value"]
                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                "fish." + saniItem: -1 * sellAmt,
                                "bells": bellsOwed,
                                "lifetimeBells": bellsOwed,
                            }
                        },
                    )

                    return await ctx.send(
                        f"{ctx.author.mention} Success! You sold **{sellAmt}x {item.lower()}** for a total of **{bellsOwed}** bells!",
                        delete_after=10,
                    )

            for name, value in user["bugs"].items():
                if not value:
                    continue
                if name == saniItem:
                    sellAmt = value if quantity > value else quantity
                    bellsOwed = sellAmt * self.bugs[saniItem]["value"]
                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                "bugs." + saniItem: -1 * sellAmt,
                                "bells": bellsOwed,
                                "lifetimeBells": bellsOwed,
                            }
                        },
                    )

                    return await ctx.send(
                        f"{ctx.author.mention} Success! You sold **{sellAmt}x {item.lower()}** for a total of **{bellsOwed}** bells!",
                        delete_after=10,
                    )

            for name, value in user["items"].items():
                if not value:
                    continue
                if name == saniItem:
                    sellAmt = value if quantity > value else quantity
                    bellsOwed = sellAmt * self.items[saniItem]["value"]
                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                "items." + saniItem: -1 * sellAmt,
                                "bells": bellsOwed,
                                "lifetimeBells": bellsOwed,
                            }
                        },
                    )

                    return await ctx.send(
                        f"{ctx.author.mention} Success! You sold **{sellAmt}x {item.lower()}** for a total of **{bellsOwed}** bells!",
                        delete_after=10,
                    )

            for name, value in user["fruit"].items():
                if value <= 0:
                    continue
                sellAmt = value if quantity > value else quantity
                if name == saniItem:
                    if saniItem == user["homeFruit"]:
                        bellsOwed += 400 * sellAmt

                    elif saniItem == 'turnip':
                        bellsOwed += 1000 * sellAmt

                    else:
                        bellsOwed += 600 * sellAmt

                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                "fruit." + saniItem: -1 * sellAmt,
                                "bells": bellsOwed,
                            }
                        },
                    )

                    return await ctx.send(
                        f"{ctx.author.mention} Success! You sold **{sellAmt}x {item.lower()}** for a total of **{bellsOwed}** bells!",
                        delete_after=10,
                    )

            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You don't have any **{item}** to sell!",
                delete_after=10,
            )

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="quests", aliases=["quest"])
    async def _quests(self, ctx, animal: typing.Optional[str]):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>",
                delete_after=10,
            )

        db = mclient.bowser.animalEvent
        await ctx.message.delete()
        user = db.find_one({"_id": ctx.author.id})

        if not db.find_one({"_id": ctx.author.id}):
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        trophyProgress = 0
        for _animal in user["animals"]:
            if _animal in user["quests"]:
                trophyProgress += 1

        if trophyProgress == 5 and not user["hasRole"]:
            db.update_one({"_id": ctx.author.id}, {"$set": {"hasRole": True}})
            mclient.bowser.users.update_one({"_id": ctx.author.id}, {"$push": {"trophies": "acevent"}})
            await ctx.send(
                f"🎉 Congrats {ctx.author.mention} 🎉! Upon looking at your account it seems you have completed a quest from every villager! You have earned the event trophy on your `!profile`, great job!"
            )

        if not animal:
            description = "Here's an overview of the requests that your island's residents have today!\n\n\n"
            animalList = []
            for x in user["animals"]:
                xQuest = self.todaysQuests[x]
                catVal = xQuest["catID"] + "." + xQuest["item"]
                cat = xQuest["catID"]
                if ctx.author.id in self.completedQuests and x in self.completedQuests[ctx.author.id]:
                    animalList.append(f'**{x}**: {xQuest["value"]}x {xQuest["itemName"]} - [COMPLETED]')

                else:
                    itemCnt = 0 if xQuest["item"] not in user[cat].keys() else user[cat][xQuest["item"]]
                    animalStr = f'**{x}**: {xQuest["value"]}x {xQuest["itemName"]} - [{itemCnt}/{xQuest["value"]}]'
                    if x not in user["quests"]:
                        animalStr = "*️⃣ " + animalStr
                    animalList.append(animalStr)

            if animalList:
                description += (
                    "\n".join(animalList)
                    + '\n\nTo talk to one of your fellow residents, simply run `!quest Name` command, replacing "Name" with who you would like to speak to or give items'
                )
            embed = discord.Embed(title="Quests", description=description)
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
            embed.set_thumbnail(url=self.items["bells"]["image"])

            return await ctx.send(ctx.author.mention, embed=embed)

        else:
            realName = animal.lower().capitalize()
            if realName not in self.animals.keys():
                return await ctx.send(
                    f"{config.redTick} {ctx.author.mention} I'm not sure who you want to look up quests for! Did you spell their name right?"
                )

            elif realName not in user["animals"]:
                return await ctx.send(
                    f'{config.redTick} {ctx.author.mention} There isn\'t anyone by the name of "{realName}" on your island!'
                )

            questInfo = self.todaysQuests[realName]
            catVal = questInfo["catID"] + "." + questInfo["item"]
            cat = questInfo["catID"]
            description = questInfo["text"]
            embed = discord.Embed(title="Quests - " + realName)
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
            embed.set_thumbnail(url=self.animals[realName]["image"])

            itemCost = int(questInfo["itemCost"])

            if ctx.author.id in self.completedQuests.keys() and realName in self.completedQuests[ctx.author.id]:
                description = (
                    "__[COMPLETED]__\n" + description + "\n\nSwing by tomorrow! I might have something for you to do"
                )
                embed.description = description
                return await ctx.send(ctx.author.mention, embed=embed)

            if cat == "fish":
                actionHint = f"You can find this item with `!fish` in <#{self.commandChannels[0]}>"

            elif cat == "bugs":
                actionHint = f"You can find this item by finding bugs from time to time in <#238081280632160257>, <#238081135865757696>, or <#671003715364192287>"

            elif cat == "fruit":
                if user["homeFruit"] != questInfo["value"]:
                    itemCost += 200

                actionHint = f"You can get this item if another player gifts it to you or if you `!harvest` it from your trees in <#{self.commandChannels[0]}>. See more info about gifting and harvesting in <#826914316846366721>"

            else:
                actionHint = f"You can find this item with `!dig` over in <#{self.commandChannels[0]}>"

            if questInfo["item"] not in user[cat].keys() or user[cat][questInfo["item"]] < questInfo["value"]:
                description += "\n\nCome back and see me when you have it!"
                if realName not in user["quests"]:
                    description = "*️⃣ " + description
                embed.description = description
                itemCnt = 0 if questInfo["item"] not in user[cat].keys() else user[cat][questInfo["item"]]
                embed.add_field(
                    name="Item request",
                    value=f'{questInfo["value"]}x {questInfo["itemName"]}\nYou have [{itemCnt}/{questInfo["value"]}] items needed. {actionHint}',
                )
                return await ctx.send(ctx.author.mention, embed=embed)

            else:
                if ctx.author.id not in self.completedQuests.keys():
                    self.completedQuests[ctx.author.id] = [realName]
                    bellInc = questInfo["value"] * itemCost
                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                catVal: -1 * questInfo["value"],
                                "bells": bellInc,
                                "lifetimeBells": bellInc,
                            },
                            "$push": {"quests": realName},
                        },
                    )
                    description = (
                        "__[COMPLETED]__\n"
                        + description
                        + f"\n\nOh! Thanks for bringing that stuff by! Here is **{bellInc}** bells for the help"
                    )
                    embed.description = description
                    return await ctx.send(ctx.author.mention, embed=embed)

                elif realName in self.completedQuests[ctx.author.id]:
                    description = (
                        "__[COMPLETED]__\n"
                        + description
                        + "\n\nSwing by tomorrow! I might have something for you to do"
                    )
                    embed.description = description
                    return await ctx.send(ctx.author.mention, embed=embed)

                else:
                    self.completedQuests[ctx.author.id].append(realName)
                    bellInc = questInfo["value"] * itemCost
                    db.update_one(
                        {"_id": ctx.author.id},
                        {
                            "$inc": {
                                catVal: -1 * questInfo["value"],
                                "bells": bellInc,
                                "lifetimeBells": bellInc,
                            },
                            "$push": {"quests": realName},
                        },
                    )
                    description = (
                        "__[COMPLETED]__\n"
                        + description
                        + f"\n\nOh! Thanks for bringing that stuff by! Here is **{bellInc}** bells for the help"
                    )
                    embed.description = description
                    return await ctx.send(ctx.author.mention, embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="fish")
    async def _fish(self, ctx):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        self.actionLock.append(ctx.author.id)
        db = mclient.bowser.animalEvent
        await ctx.message.delete()

        if not db.find_one({"_id": ctx.author.id}):
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        willBreak = False
        if self.durabilities[ctx.author.id]["fishrod"]["value"] == 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} It looks like your fishing rod is broken! It will take a bit to craft a new one, try again later"
            )

        elif self.durabilities[ctx.author.id]["fishrod"]["value"] == 1:
            willBreak = True
            self.durabilities[ctx.author.id]["fishrod"]["regenAt"] = time.time() + 3600

        self.durabilities[ctx.author.id]["fishrod"]["value"] -= 1

        catch = random.choices(
            list(self.fish.keys()),
            weights=[self.fish[x]["weight"] for x in list(self.fish.keys())],
            k=1,
        )[0]
        embed = discord.Embed(
            title="You put your fishing line in the water...",
            description="And you patiently wait for a bite...",
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        message = await ctx.send(ctx.author.mention, embed=embed)

        await asyncio.sleep(16)
        if (
            ctx.author.id in self.activeBait.keys()
            and random.choices([True, False], weights=[75, 25])[0]
            or random.choices([True, False], weights=[50, 50])[0]
        ):
            embed.set_thumbnail(url=self.fish[catch]["image"])
            description = f'You caught a **{self.rarity[self.fish[catch]["weight"]]} {self.fish[catch]["name"]}**! {self.fish[catch]["pun"]}'
            if willBreak:
                description += (
                    "\n\nWhat's this? Oh darn, __your fishing rod broke__! It will take about 1 hour to craft a new one"
                )
            embed.description = description
            db.update_one({"_id": ctx.author.id}, {"$inc": {"fish." + catch: 1}})

            await message.edit(embed=embed)

        else:
            description = "You got a bite, but whatever it was got off the line before you could reel it in. Better luck next time"
            if willBreak:
                description += (
                    "\n\nWhat's this? Oh darn, __your fishing rod broke__! It will take about 1 hour to craft a new one"
                )
            embed.description = description
            await message.edit(embed=embed)

    @_fish.error
    async def _fish_error(self, ctx, error):
        if isinstance(error, commands.MaxConcurrencyReached):  # pylint: disable=no-member
            await ctx.send(
                f"{config.redTick} {ctx.author.mention} You need two hands to fish, how can you use two lines at once? (wait until your fishing is over before trying again)",
                delete_after=10,
            )
            return await ctx.message.delete()

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="dig")
    async def _dig(self, ctx):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        await ctx.message.delete()

        if not db.find_one({"_id": ctx.author.id}):
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        willBreak = False
        if self.durabilities[ctx.author.id]["shovel"]["value"] == 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} It looks like your shovel is broken! It will take a bit to craft a new one, try again later"
            )

        elif self.durabilities[ctx.author.id]["shovel"]["value"] == 1:
            willBreak = True
            self.durabilities[ctx.author.id]["shovel"]["regenAt"] = time.time() + 3600

        self.durabilities[ctx.author.id]["shovel"]["value"] -= 1

        catch = random.choice(
            [
                "bait",
                "stick",
                "iron-nugget",
                "clay",
                "stone",
                "shell",
                "conch",
                "cowrie",
                "coral",
                "sand-dollar",
            ]
        )
        embed = discord.Embed(
            title="You used your shovel to dig up some sand...",
            description="And you found...",
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        message = await ctx.send(ctx.author.mention, embed=embed)

        await asyncio.sleep(11)
        if random.choices([True, False], weights=[65, 35])[0]:
            embed.set_thumbnail(url=self.items[catch]["image"])
            description = f'And you found 1x {self.items[catch]["name"]}'
            if willBreak:
                description += (
                    "\n\nWhat's this? Oh darn, __your shovel broke__! It will take about 1 hour to craft a new one"
                )
            embed.description = description
            db.update_one({"_id": ctx.author.id}, {"$inc": {"items." + catch: 1}})

            await message.edit(embed=embed)

        else:
            description = "And you found nothing. Well that sucks"
            if willBreak:
                description += (
                    "\n\nWhat's this? Oh darn, __your shovel broke__! It will take about 1 hour to craft a new one"
                )
            embed.description = description
            await message.edit(embed=embed)

    @_dig.error
    async def _dig_error(self, ctx, error):
        if isinstance(error, commands.MaxConcurrencyReached):  # pylint: disable=no-member
            await ctx.send(
                f"{config.redTick} {ctx.author.mention} You need two hands on a shovel, how can you use two at once? (wait until your digging is over before trying again)",
                delete_after=10,
            )
            return await ctx.message.delete()

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.group(name="use", invoke_without_command=True)
    async def _use(self, ctx):
        await ctx.message.delete()
        return

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @_use.command(name="bait")
    async def _use_bait(self, ctx):
        await ctx.message.delete()
        if ctx.channel.id not in self.commandChannels:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You need to register before using bait! Run the `!play` command in <#{self.commandChannels[0]}>",
                delete_after=10,
            )

        if "bait" not in user["items"].keys() or user["items"]["bait"] <= 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You do not have any **bait**",
                delete_after=10,
            )

        if self.durabilities[ctx.author.id]["bait"]["value"] == 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can only use one bait per day. Check back in tomorrow!"
            )

        if ctx.author.id in self.activeBait.keys():
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can only use one at a time. Check back later"
            )

        db.update_one({"_id": ctx.author.id}, {"$inc": {"items.bait": -1}})
        self.activeBait[ctx.author.id] = time.time() + 7200
        self.durabilities[ctx.author.id]
        return await ctx.send(
            f"{ctx.author.mention} You used 1 bait! You have a higher chance to catch fish for 2 hours"
        )

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="harvest")
    async def _harvest(self, ctx, fruit):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        await ctx.message.delete()
        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You need to register before harvesting! Run the `!play` command in <#{self.commandChannels[0]}>",
                delete_after=10,
            )

        fruit = fruit.lower().strip()
        if not fruit in user["unpickedFruit"].keys() or not user["unpickedFruit"][fruit]:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You don't have any **{fruit}** to harvest!",
                delete_after=10,
            )

        embed = discord.Embed(
            title="You harvest one of your trees...",
            description=f"You reach up to the **{fruit}** tree...",
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        message = await ctx.send(ctx.author.mention, embed=embed)

        quantity = -1 if not user["finished"] else -3
        if user["unpickedFruit"][fruit] - abs(quantity) <= 0:
            quantity = user["unpickedFruit"][fruit] * -1
        db.update_one(
            {"_id": ctx.author.id},
            {
                "$inc": {
                    "unpickedFruit." + fruit: quantity,
                    "fruit." + fruit: abs(quantity),
                }
            },
        )
        await asyncio.sleep(4)

        embed.description = f'You reach up to the **{fruit}** tree and pull down **{abs(quantity)}x {fruit}**! There are __{user["unpickedFruit"][fruit] - abs(quantity)}__ fruit of this type still ready to be harvested'
        await message.edit(embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="plant")
    async def _plant(self, ctx, fruit):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        user = db.find_one({"_id": ctx.author.id})
        await ctx.message.delete()
        if not user:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You need to register before planting! Run the `!play` command in <#{self.commandChannels[0]}>",
                delete_after=10,
            )

        fruit = fruit.lower().strip()
        if not fruit in user["fruit"].keys() or user["fruit"][fruit] <= 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You don't have any **{fruit}** to plant!",
                delete_after=10,
            )

        likeTrees = 0 if not fruit in user["trees"].keys() else user["trees"][fruit]
        likeSaplings = 0 if not fruit in user["saplings"].keys() else user["saplings"][fruit]
        if (likeTrees + likeSaplings) >= 50:  # Max trees
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have the maximum amount of {fruit} trees already. Try planting another type of fruit?",
                delete_after=10,
            )

        embed = discord.Embed(
            title="You begin to plant a fruit...",
            description=f"You put a **{fruit}** in the ground...",
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        message = await ctx.send(ctx.author.mention, embed=embed)
        db.update_one(
            {"_id": ctx.author.id},
            {"$inc": {"saplings." + fruit: 1, "fruit." + fruit: -1}},
        )

        await asyncio.sleep(4)
        embed.description = f'You put a **{fruit}** in the ground and a {fruit} sapling appeared in it\'s place. You have __{user["fruit"][fruit] - 1}__ left in your inventory'
        await message.edit(embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="gift")
    async def _gift(self, ctx, target: typing.Union[discord.Member, discord.User], *, item):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        targetUser = db.find_one({"_id": target.id})
        initUser = db.find_one({"_id": ctx.author.id})

        if target.id == ctx.author.id:
            return await ctx.send(f"{config.redTick} {ctx.author.mention} You can not send a gift to yourself!")

        if self.durabilities[ctx.author.id]["gift"]["value"] <= 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can make 3 gifts per day. Check back in tomorrow!"
            )

        if not targetUser:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} The user you are trying to gift to has not started their island yet! They must run the `!play` command"
            )

        if not initUser:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You haven't started your island yet! Use the `!play` command to start your vacation getaway package"
            )

        if self.durabilities[ctx.author.id]["gift"]["value"] == 0:
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You can make 3 gifts per day. Check back in tomorrow!"
            )

        items = {}
        saniItem = item.lower().strip().replace(" ", "-")

        for name, value in initUser["fish"].items():
            if value == 0:
                continue
            items[name] = "fish"

        for name, value in initUser["bugs"].items():
            if value == 0:
                continue
            items[name] = "bugs"

        for name, value in initUser["items"].items():
            if value == 0:
                continue
            items[name] = "items"

        for name, value in initUser["fruit"].items():
            if value == 0:
                continue
            items[name] = "fruit"

        if saniItem not in items.keys():
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You don't have any **{item.lower()}** in your inventory that you can gift!"
            )

        db.update_one({"_id": ctx.author.id}, {"$inc": {items[saniItem] + "." + saniItem: -1}})
        db.update_one({"_id": target.id}, {"$inc": {items[saniItem] + "." + saniItem: 1}})

        self.durabilities[ctx.author.id]["gift"]["regenAt"] = time.time() + 86400
        self.durabilities[ctx.author.id]["gift"]["value"] -= 1

        await ctx.send(
            f"Success! You have given 1 **{item.lower()}** to {target.mention}. You can only send 3 gifts per day, if you would like to send more try again tomorrow"
        )

    @commands.command(name="island")
    async def _island(self, ctx):
        if ctx.channel.id not in self.commandChannels:
            await ctx.message.delete()
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} Do not use this channel for event commands, instead one of <#{self.commandChannels[0]}>, <#{self.commandChannels[1]}>, or <#{self.commandChannels[2]}>"
            )

        db = mclient.bowser.animalEvent
        await ctx.message.delete()
        if not db.find_one({"_id": ctx.author.id}):
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You have not started your island adventure yet! Run the `!play` command to start your vacation getaway package",
                delete_after=10,
            )

        user = db.find_one({"_id": ctx.author.id})

        embed = discord.Embed(title="Your island overview")
        description = (
            f"Hello there! Happy you want to check in on how things are going! Here's a report on your island statistics:\n\n"
            f'<:bells:695408455799930991> {user["bells"]} Bells in pocket | {user["debt"]} Bells in debt'
        )

        embed.description = description
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(url=self.fruits[user["homeFruit"]])
        treeDesc = ""
        treeCnt = 0
        treeTypes = []
        for tree, value in user["trees"].items():
            treeTypes.append(tree)
            treeCnt += value
            treeDesc += str(value) + " " + tree.capitalize()
            treeDesc += " tree" if value == 1 else " trees"
            if tree in user["saplings"].keys():
                treeDesc += " | " + str(user["saplings"][tree]) + " " + tree.capitalize()
                treeDesc += " sapling" if user["saplings"][tree] == 1 else " saplings"
            treeDesc += "\n"

        for sap, value in user["saplings"].items():
            if value <= 0:
                continue
            if sap not in treeTypes:
                treeDesc += str(value) + " " + sap.capitalize()
                treeDesc += " sapling\n" if value == 1 else " saplings\n"

        embed.add_field(name="Trees", value=treeDesc)
        availFruit = []
        for name, value in user["unpickedFruit"].items():
            availFruit.append(f"{value}x " + name)

        embed.add_field(
            name="Fruit",
            value=f'Currently generating **{treeCnt * 3}** fruit per day\nUnharvested fruit: {", ".join(availFruit)}',
        )

        invList = []
        for name, value in user["fish"].items():
            if not value:
                continue
            invList.append(f"{value}x " + self.fish[name]["name"])

        for name, value in user["bugs"].items():
            if not value:
                continue
            invList.append(f"{value}x " + self.bugs[name]["name"])

        for name, value in user["items"].items():
            if not value:
                continue
            invList.append(f"{value}x " + self.items[name]["name"])

        for name, value in user["fruit"].items():
            if not value:
                continue
            invList.append(f"{value}x " + name.capitalize())

        embed.add_field(
            name="Inventory",
            value=", ".join(invList) if invList else "*No items to display*",
            inline=False,
        )
        await ctx.send(ctx.author.mention, embed=embed)

    @commands.max_concurrency(1, per=commands.BucketType.user)  # pylint: disable=no-member
    @commands.command(name="play")
    async def _signup(self, ctx, invoked=False):
        db = mclient.bowser.animalEvent
        if not invoked and db.find_one({"_id": ctx.author.id}):
            await ctx.message.delete(delay=10)
            return await ctx.send(
                f"{config.redTick} {ctx.author.mention} You've already signed up for this island adventure, why not try playing around the island? For help, see <#826914316846366721>",
                delete_after=10,
            )
        fruitList = list(self.fruits.keys())
        fruitList.remove('turnip')  # While we want to keep it for tracking, this can't be a home fruit
        homeFruit = random.choice(fruitList)
        db.insert_one(
            {
                'debt': 50000,
                "_id": invoked if invoked else ctx.author.id,
                "animals": random.sample(list(self.animals.keys()), k=5),
                "quests": [],
                "bells": 0,
                "museum": [],  # Bugs/fish donated
                "townhall": 0,  # Number status of which job currently on. 0=nothing
                "fish": {},
                "bugs": {},
                "fruit": {},
                "unpickedFruit": {homeFruit: 6},  # Two trees start with fruit, 3x fruit per tree
                "trees": {homeFruit: 2},
                "saplings": {homeFruit: 3},
                "items": {},
                "homeFruit": homeFruit,
                "hasRole": False,
                "hasBackground": False,
                "_type": "user",
                "finished": False,
                "lifetimeBells": 0,
            }
        )

        if not invoked:
            await ctx.message.delete()
            mention = ctx.author.mention
            await ctx.author.add_roles(self.eventRole)

        else:
            if ctx.guild.get_member(invoked):
                await ctx.guild.get_member(invoked).add_roles(self.eventRole)

            else:
                member = await ctx.guild.fetch_member(invoked)
                await member.add_roles(self.eventRole)

            mention = f"<@{invoked}>"

        self.durabilities[invoked if invoked else ctx.author.id] = {
            "fishrod": {"value": 25, "regenAt": None},
            "shovel": {"value": 20, "regenAt": None},
            "bait": {"value": 1, "regenAt": None},
            "gift": {"value": 3, "regenAt": None},
        }
        return await ctx.send(
            f'{mention} Thanks for signing up for your Nook Inc. Island Getaway Package, to get you started we\'ve planted some **{homeFruit}** trees for you on your island! We recommend that you check <#674357224176615455> for more information on how best to enjoy your time',
            delete_after=15,
        )

    @commands.is_owner()
    @commands.command(name="spawn")
    async def _spawn(self, ctx, catch):
        await ctx.message.delete()
        db = mclient.bowser.animalEvent
        message = ctx.message

        embed = discord.Embed(
            title="Catch the bug!",
            description=f'**{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}** has appeared! React <:net:694945150681481286> quick before it gets away!',
        )
        embed.set_thumbnail(url=self.bugs[catch]["image"])
        gameMessage = await message.channel.send(embed=embed)
        await gameMessage.add_reaction("<:net:694945150681481286>")

        await asyncio.sleep(20)
        gameMessage = await message.channel.fetch_message(gameMessage.id)
        userList = []
        for reaction in gameMessage.reactions:
            if str(reaction) == "<:net:694945150681481286>":
                users = await reaction.users().flatten()
                for user in users:
                    if user.bot:
                        continue
                    if not db.find_one({"_id": user.id}):
                        await self._signup.__call__(message.channel, user.id)  # pylint: disable=not-callable

                    db.update_one({"_id": user.id}, {"$inc": {"bugs." + catch: 1}})
                    userList.append(user)

        if userList:
            embed.description = (
                ", ".join([x.mention for x in userList])
                + f'{" all" if len(userList) > 1 else ""} caught one **{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}**! {self.bugs[catch]["pun"]}'
            )

        else:
            embed.description = f'No one caught the **{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}** in time, it got away!'

        await gameMessage.edit(embed=embed)

    @_donate.error
    @_pay.error
    @_sell.error
    @_quests.error
    @_use_bait.error
    @_harvest.error
    @_plant.error
    @_gift.error
    @_signup.error
    async def _generic_errors(self, ctx, error):
        if isinstance(error, commands.MaxConcurrencyReached):  # pylint: disable=no-member
            await ctx.send(
                f"{config.redTick} {ctx.author.mention} Please wait before using that command again",
                delete_after=10,
            )
            return await ctx.message.delete()

        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"{config.redTick} {ctx.author.mention} You are missing a part of the command. Check <#826914316846366721> command usage",
                delete_after=10,
            )
            return await ctx.message.delete()

        elif isinstance(error, commands.UserInputError):
            await ctx.send(
                f"{config.redTick} {ctx.author.mention} That is the incorrect usage of the command. Check <#826914316846366721> command usage",
                delete_after=10,
            )
            return await ctx.message.delete()

        else:
            raise error

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        # if message.channel.id not in [
        #    238081280632160257,
        #    238081135865757696,
        #    671003715364192287,
        # ]:
        if message.channel.id != 769666021706432532:
            return  # general, switch-discussion, animal-crossing

        if not random.choices([True, False], weights=[1, 99])[0]:
            return

        db = mclient.bowser.animalEvent
        catch = random.choices(
            list(self.bugs.keys()),
            weights=[self.bugs[x]["weight"] for x in list(self.bugs.keys())],
            k=1,
        )[0]

        embed = discord.Embed(
            title="Catch the bug!",
            description=f'**{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}** has appeared! React <:net:694945150681481286> quick before it gets away!',
        )
        embed.set_thumbnail(url=self.bugs[catch]["image"])
        gameMessage = await message.channel.send(embed=embed)
        await gameMessage.add_reaction("<:net:694945150681481286>")

        await asyncio.sleep(20)
        gameMessage = await message.channel.fetch_message(gameMessage.id)
        userList = []
        for reaction in gameMessage.reactions:
            if str(reaction) == "<:net:694945150681481286>":
                users = await reaction.users().flatten()
                for user in users:
                    if user.bot:
                        continue
                    if not db.find_one({"_id": user.id}):
                        await self._signup.__call__(message.channel, user.id)  # pylint: disable=not-callable

                    db.update_one({"_id": user.id}, {"$inc": {"bugs." + catch: 1}})
                    userList.append(user)

        if userList:
            embed.description = (
                ", ".join([x.mention for x in userList])
                + f'{" all" if len(userList) > 1 else ""} caught one **{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}**! {self.bugs[catch]["pun"]}'
            )

        else:
            embed.description = f'No one caught the **{self.rarity[self.bugs[catch]["weight"]]} {self.bugs[catch]["name"]}** in time, it got away!'

        await gameMessage.edit(embed=embed)


def setup(bot):
    bot.add_cog(AnimalGame(bot))
    logging.info("[Extension] Animal Crossing Event module loaded")


def teardown(bot):
    bot.remove_cog("AnimalGame")
    logging.info("[Extension] Animal Crossing Event module unloaded")
