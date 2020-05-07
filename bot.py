import asyncio
import logging
import sys
import argparse

import pymongo
import tornado.ioloop
import tornado.web
import tornado
import discord
from discord.ext import commands

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)
activityStatus = discord.Activity(type=discord.ActivityType.watching, name='over the server')
bot = commands.Bot(['!', ',', '.'], max_messages=300000, fetch_offline_members=True, activity=activityStatus, case_insensitive=True)

LOG_FORMAT = '%(levelname)s [%(asctime)s]: %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

class BotCache(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.READY = False

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info('[Bot] on_ready')
        if not self.READY:
            self.bot.load_extension('modules.core')
            #self.READY = True
            #return
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
                    await utils.store_user(member)
                    continue

                roleList = []
                for role in member.roles:
                    if role.id != NS.id:
                        roleList.append(role.id)

                if roleList == doc['roles']:
                    continue

                db.update_one({'_id': member.id}, {'$set': {
                    'roles': roleList
                        }})

            logging.info('[Cache] Inital database syncronization complete')
            self.READY = True

async def setup_discord():
    bot.add_cog(BotCache(bot))
    bot.load_extension('jishaku')
    await bot.start(config.token)

async def safe_send_message(channel, content=None, embeds=None):
    await channel.send(content, embed=embeds)

@bot.event
async def on_message(message):
    return # Return so commands will not process, and main extension can process instead

class MainHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Content-Type', 'text/plain; charset=UTF-8')

    async def get(self, archiveID):
        db = mclient.bowser.archive
        doc = db.find_one({'_id': archiveID})

        if not doc:
            return self.write('# No archive exists for this ID or it is expired')

        else:
            self.write(doc['body'])

if __name__ == '__main__':
    print('\033[94mFils-A-Mech python by MattBSG#8888 2019\033[0m')
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-only', action='store_true')
    parser.add_argument('--bot-only', action='store_true')
    args = parser.parse_args()

    logging.info('Initializing web framework')
    app = tornado.web.Application([
        (r'/api/archive/([0-9]+-[0-9]+)', MainHandler)
    ], xheader=True)

    if args.bot_only:
        logging.info('[Bot] Running in bot only mode, web will not initalize')
        bot.add_cog(BotCache(bot))
        bot.load_extension('jishaku')
        bot.run(config.token)

    elif args.web_only:
        logging.info('[Web] Running in web only mode, discord will not initialize')
        app.listen(8880)
        tornado.ioloop.IOLoop.current().start()

    else:
        logging.info('[Bot] Running in hybrid mode, initializing bot and web')
        app.listen(8880)
        tornado.ioloop.IOLoop.current().run_sync(setup_discord)
        tornado.ioloop.IOLoop.current().start()
