import logging
from sys import exit

import discord
import pymongo
from discord.ext import commands


LOG_FORMAT = '%(levelname)s [%(asctime)s]: %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

import tools


try:
    import config

except ImportError:
    logging.critical('[Bot] config.py does not exist, you should make one from the example config')
    exit(1)

mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)
intents = discord.Intents(
    guilds=True, members=True, bans=True, emojis=True, voice_states=True, presences=True, messages=True, reactions=True
)
activityStatus = discord.Activity(type=discord.ActivityType.watching, name='over the server')
bot = commands.Bot(
    config.command_prefixes,
    intents=intents,
    max_messages=300000,
    fetch_offline_members=True,
    activity=activityStatus,
    case_insensitive=True,
)


class BotCache(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.READY = False

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info('[Bot] on_ready')
        if not self.READY:
            self.bot.load_extension('modules.core')
            # self.READY = True
            # return
            logging.info('[Cache] Performing initial database synchronization')
            db = mclient.bowser.users
            NS = bot.get_guild(config.nintendoswitch)

            guildCount = len(NS.members)
            userCount = 0
            for member in NS.members:
                userCount += 1
                logging.debug(f'[Cache] Syncronizing user {userCount}/{guildCount}')
                doc = db.find_one({'_id': member.id})
                if not doc:
                    await tools.store_user(member)
                    continue

                roleList = []
                for role in member.roles:
                    if role.id != NS.id:
                        roleList.append(role.id)

                if roleList == doc['roles']:
                    continue

                db.update_one({'_id': member.id}, {'$set': {'roles': roleList}})

            logging.info('[Cache] Inital database syncronization complete')
            self.READY = True


class AutomodSubstitute(commands.Cog):
    # If antispam is not loaded, ensure on_automod_finished() from utility.py will run'''

    def __init__(self, bot):
        self.bot = bot
        self.READY = False
        self.antispam_loaded = False

    def set_antispam_loaded(self):
        self.antispam_loaded = True

    @commands.Cog.listener()
    async def on_message(self, message):
        if not self.antispam_loaded:
            await self.bot.get_cog('Utility Commands').on_automod_finished(message)


async def safe_send_message(channel, content=None, embeds=None):
    await channel.send(content, embed=embeds)


@bot.event
async def on_message(message):
    return  # Return so commands will not process, and main extension can process instead


if __name__ == '__main__':
    print('\033[94mMechaBowser by MattBSG#8888 2019\033[0m')

    bot.add_cog(BotCache(bot))
    bot.add_cog(AutomodSubstitute(bot))
    bot.load_extension('jishaku')
    bot.run(config.token)
