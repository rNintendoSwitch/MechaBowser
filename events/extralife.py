import datetime
import logging

import discord
import requests
from discord.ext import commands, tasks


class ExtraLife(commands.Cog):
    def __init__(self, bot):
        ################################################################################################################################
        self.GUILD = 238080556708003851

        # Donation Alert Consts
        self.EXTRA_LIFE_ADMIN = 772464126483890227
        self.EXTRA_LIFE = 654018662860193830
        self.GENERAL = 238081280632160257
        self.DONATIONS = 774672505540968468
        self.DONATIONS_URL = 'https://extra-life.org/api/participants/409108/donations'
        self.FOOTER_LINKS = '[Watch live on Twitch](https://twitch.tv/rNintendoSwitch)\n[Donate to Children\'s Miracle Network Hospitals](https://rNintendoSwitch.com/donate)'

        # Role adding consts
        self.CHAT_CHANNEL = 654018662860193830
        self.CHAT_ROLE = 772481541657985045
        ################################################################################################################################

        self.bot = bot
        self.guild = self.bot.get_guild(self.GUILD)
        self.extra_life_admin = self.guild.get_channel(self.EXTRA_LIFE_ADMIN)
        self.extra_life = self.guild.get_channel(self.EXTRA_LIFE)
        self.general = self.guild.get_channel(self.GENERAL)
        self.donations = self.guild.get_channel(self.DONATIONS)
        self.chatRole = self.guild.get_role(self.CHAT_ROLE)
        self.lastDonationID = None

        self.donation_check.start()

    @commands.command(name='ldi')
    @commands.check_any(commands.is_owner(), commands.has_guild_permissions(administrator=True))
    async def lastdonorid(self, ctx, string: str = None):
        if string is None:
            return await ctx.send(content=f'Last donation id is `{self.lastDonationID}`')

        self.lastDonationID = string
        return await ctx.send(content=f'Last donation id set to `{string}`')

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.channel.id != self.CHAT_CHANNEL:
            return
        if self.chatRole in message.author.roles:
            return

        await message.author.add_roles(self.chatRole)

    @tasks.loop(seconds=30)
    async def donation_check(self):
        donations_request = requests.get(self.DONATIONS_URL, timeout=8.0)

        try:
            donations_request.raise_for_status()
        except Exception as e:
            logging.error(e)

        donations = donations_request.json()
        donation_embeds = []

        for donation in donations:
            if donation['donationID'] == self.lastDonationID:
                break

            # If we have no saved donation, assume we're upto date
            if self.lastDonationID is None:
                break

            donor_name = 'Anonymous' if not 'displayName' in donation else donation['displayName']
            donation_time = datetime.datetime.strptime(
                donation['createdDateUTC'], '%Y-%m-%dT%H:%M:%S.%f%z'
            )  # eg 2020-11-07T06:00:07.327+0000

            embed = discord.Embed(
                title="Extra Life Donation Alert!", colour=discord.Color(8378422), timestamp=donation_time
            )
            embed.add_field(name="From", value=donor_name, inline=True)

            if 'amount' in donation:
                embed.add_field(name="Amount", value='${:0,.2f}'.format(donation['amount']), inline=True)

            if 'message' in donation:
                embed.add_field(name="Message", value=donation['message'], inline=False)

            embed.add_field(name="\uFEFF", value=self.FOOTER_LINKS, inline=False)  # ZERO WIDTH NO-BREAK SPACE (U+FEFF)
            donation_embeds.append(embed)
            logging.info(f'Sending donation {donation["donationID"]} from {donor_name}')

        donation_embeds.reverse()
        for embed in donation_embeds:
            await self.extra_life.send(embed=embed)
            await self.general.send(embed=embed)
            await self.donations.send(embed=embed)

        self.lastDonationID = donations[0]['donationID']

    def cog_unload(self):
        self.donation_check.cancel()  # pylint: disable=no-member


def setup(bot):
    bot.add_cog(ExtraLife(bot))
    logging.info('[Extension] ExtraLife module loaded')


def teardown(bot):
    bot.remove_cog('ExtraLife')
    logging.info('[Extension] ExtraLife module unloaded')
