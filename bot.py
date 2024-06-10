import asyncio
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

mclient = pymongo.MongoClient(config.mongoURI)
intents = discord.Intents(
    guilds=True,
    members=True,
    bans=True,
    emojis=True,
    voice_states=True,
    presences=True,
    messages=True,
    message_content=True,
    reactions=True,
)


class BotCache(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.READY = False

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info('[Bot] on_ready')
        if not self.READY:
            await self.bot.load_extension('modules.core')
            # self.READY = True
            # return
            logging.info('[Cache] Performing initial database synchronization')
            db = mclient.bowser.users
            NS = self.bot.get_guild(config.nintendoswitch)

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


class MechaBowser(commands.Bot):
    def __init__(self):
        super().__init__(
            activity=discord.Activity(type=discord.ActivityType.watching, name='over the server'),
            case_insensitive=True,
            command_prefix=config.command_prefixes,
            chunk_guilds_at_startup=True,
            intents=discord.Intents(
                guilds=True,
                members=True,
                bans=True,
                emojis=True,
                voice_states=True,
                presences=True,
                messages=True,
                message_content=True,
                reactions=True,
            ),
            max_messages=300000,
        )

        if config.DSN:
            from discord_sentry_reporting import use_sentry

            use_sentry(self, dsn=config.DSN, traces_sample_rate=1.0, environment='production')

    async def setup_hook(self):
        await self.add_cog(BotCache(self))
        await self.add_cog(AutomodSubstitute(self))
        await self.load_extension('jishaku')

    async def on_message(self, message):
        return  # Return so commands will not process, and main extension can process instead

bot = MechaBowser()
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, exception):
    if isinstance(exception, discord.app_commands.MissingRole) or isinstance(exception, discord.app_commands.MissingAnyRole):
        return await interaction.response.send_message(f'{config.redTick} You do not have permission to run this command', ephemeral=True)

    elif isinstance(exception, discord.app_commands.CommandOnCooldown):
        return await interaction.response.send_message(f'{config.redTick} This command is on cooldown, please wait {exception.retry_after} seconds and try again', ephemeral=True)

    elif isinstance(exception, discord.app_commands.CommandSignatureMismatch):
        await interaction.response.send_message(f'{config.redTick} A temporary error occured when running that command. Please wait a bit, then try again', ephemeral=True)
        logging.error(f'A command signature mismatch has occured, we will attempt to resync. Raising triggering exception')

        guildObj = discord.Object(id=config.nintendoswitch)
        await interaction.client.tree.sync(guild=guildObj)
        raise exception

    else:
        # Unhandled, error to user and raise
        await interaction.response.send_message(f'{config.redTick} A temporary error occured when running that command. Please wait a bit, then try again', ephemeral=True)
        raise exception


if __name__ == '__main__':
    print('\033[94mMechaBowser by MattBSG#8888 2019\033[0m')
    asyncio.run(bot.start(config.token))
