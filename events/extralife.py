import logging
import re
from datetime import datetime
from typing import List

import config
import discord
import pymongo
import requests
from discord.ext import commands, tasks

import tools


class ExtraLife(commands.Cog):
    def __init__(self, bot):
        ################################################################################################################################
        self.GUILD = 238080556708003851

        # Donation Alert Consts
        self.EXTRA_LIFE_ADMIN = 772464126483890227
        self.EXTRA_LIFE = 654018662860193830
        self.GENERAL = 238081280632160257
        self.DONATIONS = 774672505540968468
        self.DONATIONS_URL = 'https://extra-life.org/api/participants/531641/donations'
        self.FOOTER_LINKS = '[Watch live on Twitch](https://twitch.tv/rNintendoSwitch)\n[Donate to Children\'s Miracle Network Hospitals](https://rNintendoSwitch.com/donate)'

        # Role adding consts
        self.CHAT_CHANNEL = 654018662860193830
        self.CHAT_ROLE = 1059140680682844160
        self.DONOR_ROLE = 1192235806551716044

        # Trophy and Background consts
        self.TROPHY = 'extra-life-2024'
        self.BACKGROUND = 'extra-life'

        # Donation incentive ID consts
        self.INCENTIVES = {
            # 'uuiduuid-uuid-uuid-uuiduuiduuiduuid': 'Friendly Name',
        }

        ################################################################################################################################

        self.mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)
        self.bot = bot
        self.guild = self.bot.get_guild(self.GUILD)
        self.extra_life_admin = self.guild.get_channel(self.EXTRA_LIFE_ADMIN)
        self.extra_life = self.guild.get_channel(self.EXTRA_LIFE)
        self.general = self.guild.get_channel(self.GENERAL)
        self.donations = self.guild.get_channel(self.DONATIONS)
        self.chatRole = self.guild.get_role(self.CHAT_ROLE)
        self.donorRole = self.guild.get_role(self.DONOR_ROLE)
        self.lastDonationID = None

        self.donation_check.start()

    @commands.command(name='ldi')
    @commands.check_any(commands.is_owner(), commands.has_guild_permissions(administrator=True))
    async def lastdonorid(self, ctx, string: str = None):
        if string is None:
            return await ctx.send(content=f'Last donation id is `{self.lastDonationID}`')

        self.lastDonationID = string
        return await ctx.send(content=f'Last donation id set to `{string}`')

    @commands.group(name='elperks', invoke_without_command=False)
    async def _perks(self, ctx):
        pass

    @_perks.command(name='grant')
    @commands.check_any(commands.is_owner(), commands.has_guild_permissions(view_audit_log=True))
    async def perks_grant(self, ctx, members: commands.Greedy[tools.ResolveUser]):
        errors = 0
        msg = await ctx.send(f'{config.loading} Granting Extra Life perks to {len(members)} member(s)...')
        for member in members:
            try:
                await self._assign_properties(member)

            except ValueError:
                errors += 1

        if errors == len(members):
            return await msg.edit(content=f'{config.redTick} Extra Life perks granted to 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} Extra Life perks granted to {len(members) - errors}/{len(members)} member(s).'
            )

    @_perks.command(name='revoke')
    @commands.check_any(commands.is_owner(), commands.has_guild_permissions(view_audit_log=True))
    async def perks_revoke(self, ctx, members: commands.Greedy[tools.ResolveUser]):
        errors = 0
        msg = await ctx.send(f'{config.loading} Revoking Extra Life perks to {len(members)} member(s)...')
        for member in members:
            try:
                await self._remove_properties(member)

            except ValueError:
                errors += 1

        if errors == len(members):
            return await msg.edit(content=f'{config.redTick} Extra Life perks revoked from 0 members')

        else:
            return await msg.edit(
                content=f'{config.greenTick} Extra Life perks revoked from {len(members) - errors}/{len(members)} member(s).'
            )

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
            match = re.match(r'[\s\S]+#\d{4}|[a-z0-9._]+', donor_name)
            if match:
                # Donor name format matches a Discord username
                member = discord.utils.find(lambda m: str(m) == match.group(0), self.guild.members)
                if member:
                    if self.donorRole not in member.roles:
                        try:
                            await self._assign_properties(member)

                        except ValueError:
                            await self.extra_life_admin.send(
                                f':warning: An error occured while attempting to grant donation benefits to `{donor_name}`, they already have the background or trophy'
                            )

                    donor_name = donor_name + f' ({member.mention})'

            donation_time = datetime.strptime(
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

            if 'incentiveID' in donation and donation['incentiveID'] in self.INCENTIVES.keys():
                # If an incentive, double check it matches what we know
                embed.add_field(name="Incentive claimed", value=self.INCENTIVES[donation['incentiveID']])

            embed.add_field(name="\uFEFF", value=self.FOOTER_LINKS, inline=False)  # ZERO WIDTH NO-BREAK SPACE (U+FEFF)
            donation_embeds.append(embed)
            logging.info(f'Sending donation {donation["donationID"]} from {donor_name}')

        donation_embeds.reverse()
        for embed in donation_embeds:
            await self.extra_life_admin.send(embed=embed)
            await self.extra_life.send(embed=embed)
            await self.general.send(embed=embed)
            await self.donations.send(embed=embed)

        self.lastDonationID = donations[0]['donationID']

    async def _assign_properties(self, member: discord.Member):
        await member.add_roles(self.donorRole)
        await tools.commit_profile_change(self.bot, member, 'trophy', self.TROPHY)
        await tools.commit_profile_change(self.bot, member, 'background', self.BACKGROUND)

    async def _remove_properties(self, member: discord.Member):
        await member.remove_roles(self.donorRole)
        await tools.commit_profile_change(self.bot, member, 'trophy', self.TROPHY, revoke=True)
        await tools.commit_profile_change(self.bot, member, 'background', self.BACKGROUND, revoke=True)

    def cog_unload(self):
        self.donation_check.cancel()  # pylint: disable=no-member


async def setup(bot):
    await bot.add_cog(ExtraLife(bot))
    logging.info('[Extension] ExtraLife module loaded')


async def teardown(bot):
    await bot.remove_cog('ExtraLife')
    logging.info('[Extension] ExtraLife module unloaded')
