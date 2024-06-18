import asyncio
import io
import logging
import pathlib
import re
import time
import typing
import urllib.parse
from datetime import datetime, timezone

import aiohttp
import config
import discord
import pymongo
from discord import Webhook, WebhookType, app_commands
from discord.ext import commands, tasks
from fuzzywuzzy import process

import tools


mclient = pymongo.MongoClient(config.mongoURI)

serverLogs = None
modLogs = None


class ChatControl(commands.Cog, name='Utility Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.boostChannel = self.bot.get_channel(config.boostChannel)
        self.affiliateTags = {
            "*": ["awc"],
            "amazon.*": ["colid", "coliid", "tag", "ascsubtag"],
            "bestbuy.*": ["aid", "cjpid", "lid", "pid"],
            "bhphotovideo.com": ["sid"],
            "ebay.*": ["afepn", "campid", "pid"],
            "gamestop.com": ["affid", "cid", "sourceid"],
            "groupon.*": ["affid"],
            "newegg*.*": ["aid", "pid"],
            "play-asia.com": ["tagid"],
            "stacksocial.com": ["aid", "rid"],
            "store.nintendo.co.uk": ["affil"],
            "tigerdirect.com": ["affiliateid", "srccode"],
            "walmart.*": ["sourceid", "veh", "wmlspartner"],
        }

        # Add context menus to command tree
        self.historyContextMenu = app_commands.ContextMenu(
            name='View History', callback=self._pull_history, type=discord.AppCommandType.user
        )
        self.bot.tree.add_command(self.historyContextMenu, guild=discord.Object(id=config.nintendoswitch))

    # Called after automod filter finished, because of the affilite link reposter. We also want to wait for other items in this function to complete to call said reposter.
    async def on_automod_finished(self, message):
        if message.type == discord.MessageType.premium_guild_subscription:
            boost_message = message.system_content.replace(
                message.author.name, f'{message.author.name} ({message.author.mention})'
            )
            await self.adminChannel.send(boost_message)
            await self.boostChannel.send(boost_message)

        if message.author.bot or message.type not in [discord.MessageType.default, discord.MessageType.reply]:
            logging.debug(f'on_automod_finished discarding non-normal-message: {message.type=}, {message.id=}')
            return

        # Filter and clean affiliate links
        # We want to call this last to ensure all above items are complete.
        links = tools.linkRe.finditer(message.content)
        if links:
            contentModified = False
            content = message.content
            for link in links:
                linkModified = False

                try:
                    urlParts = urllib.parse.urlsplit(link[0])
                except ValueError:  # Invalid URL edge case
                    continue

                urlPartsList = list(urlParts)

                query_raw = dict(urllib.parse.parse_qsl(urlPartsList[3]))
                # Make all keynames lowercase in dict, this shouldn't break a website, I hope...
                query = {k.lower(): v for k, v in query_raw.items()}

                # For each domain level of hostname, eg. foo.bar.example => foo.bar.example, bar.example, example
                labels = urlParts.hostname.split(".")
                for i in range(0, len(labels)):
                    domain = ".".join(labels[i - len(labels) :])

                    # Special case: rewrite 'amazon.*/exec/obidos/ASIN/.../' to 'amazon.*/dp/.../'
                    if pathlib.PurePath(domain).match('amazon.*'):
                        match = re.match(r'^/exec/obidos/ASIN/(\w+)/.*$', urlParts.path)
                        if match:
                            linkModified = True
                            urlPartsList[2] = f'/dp/{match.group(1)}'  # 2 = path

                    for glob, tags in self.affiliateTags.items():
                        if pathlib.PurePath(domain).match(glob):
                            for tag in tags:
                                if tag in query:
                                    linkModified = True
                                    query.pop(tag, None)

                if linkModified:
                    urlPartsList[3] = urllib.parse.urlencode(query)
                    url = urllib.parse.urlunsplit(urlPartsList)

                    contentModified = True
                    content = content.replace(link[0], url)

            if contentModified:
                useHook = None
                for h in await message.channel.webhooks():
                    if h.type == WebhookType.incoming and h.token:
                        useHook = h

                if not useHook:
                    # An incoming webhook does not exist
                    useHook = await message.channel.create_webhook(
                        name=f'mab_{message.channel.id}',
                        reason='No webhooks existed; 1 or more is required for affiliate filtering',
                    )

                async with aiohttp.ClientSession() as session:
                    webhook = Webhook.from_url(useHook.url, session=session)
                    webhook_message = await webhook.send(
                        content=content,
                        username=message.author.display_name,
                        avatar_url=message.author.display_avatar.url,
                        wait=True,
                    )

                    try:
                        await message.delete()
                    except Exception:
                        pass

                    embed = discord.Embed(
                        description='The above message was automatically reposted by Mecha Bowser to remove an affiliate marketing link. The author may react with 🗑️ to delete these messages.'
                    )

                    # #mab_remover is the special sauce that allows users to delete their messages, see on_raw_reaction_add()
                    icon_url = (
                        f'{message.author.display_avatar.url}#mab_remover_{message.author.id}_{webhook_message.id}'
                    )
                    embed.set_footer(text=f'Author: {str(message.author)} ({message.author.id})', icon_url=icon_url)

                    # A seperate message is sent so that the original message has embeds
                    embed_message = await message.channel.send(embed=embed)
                    await embed_message.add_reaction('🗑️')

    # Handle :wastebasket: reactions for user deletions on messages reposted on a user's behalf
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if not payload.member:
            return  # Not in a guild
        if payload.emoji.name != '🗑️':
            return  # Not a :wastebasket: emoji
        if payload.user_id == self.bot.user.id:
            return  # This reaction was added by this bot

        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        embed = None if not message.embeds else message.embeds[0]

        if message.author.id != self.bot.user.id:
            return  # Message is not from the bot
        if not embed:
            return  # Message does not have an embed

        allowed_remover = None
        target_message = None
        # Search for special url tag in footer/author icon urls:
        # ...#mab_remover_{remover} or ..#mab_remover_{remover}_{message}
        for icon_url in [embed.author.icon_url, embed.footer.icon_url]:
            if not icon_url:
                continue  # Location does not have an icon_url

            match = re.search(r'#mab_remover_(\d{15,25})(?:_(\d{15,25}))?$', icon_url)
            if not match:
                continue  # No special url tag here

            allowed_remover = match.group(1)
            target_message = match.group(2)
            break

        if not allowed_remover:  # No special url tag detected
            return
        if str(payload.user_id) != str(allowed_remover):  # Reactor is not the allowed remover
            try:
                await message.remove_reaction(payload.emoji, payload.member)
            except:
                pass
            return

        try:
            if target_message:
                msg = await channel.fetch_message(target_message)
                await msg.delete()

            await message.delete()
        except Exception as e:
            logging.warning(e)
            pass

    # Large block of old event commented out code was removed on 12/02/2020
    # Includes: Holiday season celebration, 30k members celebration, Splatoon splatfest event, Pokemon sword/shield event
    # https://github.com/rNintendoSwitch/MechaBowser/commit/373cef69aa5b9da7fe5945599b7dde387caf0700

    #    @commands.command(name='archive')
    #    async def _archive(self, ctx, members: commands.Greey[discord.Member], channels: commands.Greedy[discord.Channel], limit: typing.Optional[int] = 200, channel_limiter: typing.Greedy[discord.Channel]):
    #        pass

    @app_commands.command(name='clean', description='Delete upto 2000 messages, optionally only from 1 or more users')
    @app_commands.describe(
        count='The number of messages to search for and delete that match the user filter',
        users='One or more space separated user IDs that are the target of the clean',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _clean(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 2000],
        users: typing.Optional[str] = '',
    ):
        await interaction.response.defer()
        users = users.split()
        deleteUsers = []
        invalidUsers = []
        for u in users:
            try:
                u = int(u)

            except ValueError:
                # Bad ID passed
                invalidUsers.append(u)
                continue

            user = self.bot.get_user(u)
            if not user:
                try:
                    user = await self.bot.fetch_user(u)

                except (discord.NotFound, discord.HTTPException):
                    invalidUsers.append(u)
                    continue

            deleteUsers.append(user)

        if len(invalidUsers) == len(users) and len(users) > 0:
            # All provided users are invalid, raise to user
            return await interaction.followup.send(
                f'{config.redTick} All users provided are invalid. Please check your input and try again',
                ephemeral=True,
            )

        if count >= 100:
            view = tools.RiskyConfirmation(timeout=15)
            view.message = await interaction.followup.send(
                f'This action will scan and delete up to {count} messages, are you sure you want to proceed?',
                view=view,
                wait=True,
            )
            await view.wait()

            if view.timedout:
                await view.message.edit(content='Confirmation timed out, clean action canceled.', view=view)
                return await view.message.delete(delay=5)

            if not view.value:
                # Canceled by user
                await view.message.edit(content='Clean action canceled.')
                return await view.message.delete(delay=5)

            else:
                await view.message.delete()

        userList = None if not deleteUsers else [x.id for x in deleteUsers]

        def message_filter(message):
            return True if not userList or message.author.id in userList else False

        deleted = await interaction.channel.purge(limit=count, check=message_filter, bulk=True)

        try:
            await interaction.delete_original_response()

        except:
            # Message may not exist
            pass

        if count >= 100:
            # Original interaction deleted
            m = await interaction.channel.send(f'{config.greenTick} Clean action complete')

        else:
            # Original interaction still available, need to use followup
            m = await interaction.followup.send(f'{config.greenTick} Clean action complete', wait=True)

        return await m.delete(delay=5)

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class SlowmodeCommand(app_commands.Group):
        pass

    slowmode_group = SlowmodeCommand(name='slowmode', description='Change slowmode settings for a channel')

    @slowmode_group.command(name='set', description='Enable a slowmode in a channel for a given duration')
    @app_commands.describe(
        duration='The slowmode message duration',
        channel='The channel to set in slowmode. If left blank, defaults to the channel the command is run in',
    )
    async def _slowmode(
        self, interaction: discord.Interaction, duration: str, channel: typing.Optional[discord.TextChannel]
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if not channel:
            channel = interaction.channel

        try:
            time, seconds = tools.resolve_duration(duration, include_seconds=True)
            time = tools.humanize_duration(time)
            seconds = int(seconds)
            if seconds < 1:
                return interaction.followup.send(
                    f'{config.redTick} You cannot set the duration to less than one second. If you would like to clear the slowmode, use the `/slowmode clear` command'
                )

            elif seconds > 60 * 60 * 6:  # Six hour API limit
                return interaction.followup.send(f'{config.redTick} You cannot set the duration greater than six hours')

        except KeyError:
            return await interaction.send(f'{config.redTick} Invalid duration passed')

        if channel.slowmode_delay == seconds:
            return await interaction.send(f'{config.redTick} The slowmode is already set to {time}')

        await channel.edit(slowmode_delay=seconds, reason=f'{interaction.user} has changed the slowmode delay')
        await channel.send(
            f':stopwatch: This channel now has a **{time}** slowmode in effect. Please be mindful of spam per the server rules'
        )

        await interaction.followup.send(f'{config.greenTick} {channel.mention} now has a {time} slowmode')

    @slowmode_group.command(name='clear', description='Remove any active slowmode in a given channel')
    @commands.has_any_role(config.moderator, config.eh)
    async def _slowmode_clear(self, interaction: discord.Interaction, channel: typing.Optional[discord.TextChannel]):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if not channel:
            channel = interaction.channel

        if channel.slowmode_delay == 0:
            return await interaction.followup.send(f'{config.redTick} {channel.mention} is not under a slowmode')

        await channel.edit(slowmode_delay=0, reason=f'{interaction.user} has removed the slowmode delay')
        await channel.send(
            f':stopwatch: Slowmode for this channel is no longer in effect. Please be mindful of spam per the server rules'
        )

        return await interaction.followup.send(f'{config.greenTick} {channel.mention} no longer has slowmode')

    @app_commands.command(name='info', description='Get an overview of a user')
    @app_commands.describe(user='The user you wish to grab info on')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _info(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        inServer = True
        dbUser = mclient.bowser.users.find_one({'_id': user.id})

        if not dbUser:
            inServer = False
            desc = (
                f'Fetched information about {user.mention} from the API because they are not in this server. '
                'There is little information to display as they have not been recorded joining the server before'
            )

            infractions = mclient.bowser.puns.find({'user': user.id}).count()
            if infractions:
                desc += f'\n\nUser has {infractions} infraction entr{"y" if infractions == 1 else "ies"}, use `/history {user.id}` to view'

            embed = discord.Embed(color=discord.Color(0x18EE1C), description=desc)
            embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name='Created', value=f'<t:{int(user.created_at.timestamp())}:f>')

            return await interaction.followup.send(embed=embed)

        # Member object, loads of info to work with
        messages = mclient.bowser.messages.find({'author': user.id})
        msgCount = 0 if not messages else messages.count()

        desc = (
            f'Fetched user {user.mention}.'
            if inServer
            else (
                f'Fetched information about previous member {user.mention} '
                'from the API because they are not in this server. '
                'Showing last known data from before they left'
            )
        )

        embed = discord.Embed(color=discord.Color(0x18EE1C), description=desc)
        embed.set_author(name=f'{str(user)} | {user.id}', icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name='Messages', value=str(msgCount), inline=True)
        if inServer:
            embed.add_field(name='Join date', value=f'<t:{int(user.joined_at.timestamp())}:f>', inline=True)
        roleList = []
        if inServer:
            for role in reversed(user.roles):
                if role.id == user.guild.id:
                    continue

                roleList.append(role.mention)

        else:
            roleList = dbUser['roles']

        if not roleList:
            # Empty; no roles
            roles = '*User has no roles*'

        else:
            if not inServer:
                tempList = []
                for x in reversed(roleList):
                    y = interaction.guild.get_role(x)
                    name = '*deleted role*' if not y else y.mention
                    tempList.append(name)

                roleList = tempList

            roles = ', '.join(roleList)

        embed.add_field(name='Roles', value=roles, inline=False)

        lastMsg = (
            'N/a' if msgCount == 0 else f'<t:{int(messages.sort("timestamp", pymongo.DESCENDING)[0]["timestamp"])}:f>'
        )
        embed.add_field(name='Last message', value=lastMsg, inline=True)
        embed.add_field(name='Created', value=f'<t:{int(user.created_at.timestamp())}:f>', inline=True)

        noteDocs = mclient.bowser.puns.find({'user': user.id, 'type': 'note'})
        fieldValue = 'View history to get full details on all notes\n\n'
        if noteDocs.count():
            noteCnt = noteDocs.count()
            noteList = []
            for x in noteDocs.sort('timestamp', pymongo.DESCENDING):
                stamp = f'[<t:{int(x["timestamp"])}:d>]'
                noteContent = f'{stamp}: {x["reason"]}'

                fieldLength = 0
                for value in noteList:
                    fieldLength += len(value)
                if len(noteContent) + fieldLength > 924:
                    fieldValue = f'Only showing {len(noteList)}/{noteCnt} notes. ' + fieldValue
                    break

                noteList.append(noteContent)

            embed.add_field(name='User notes', value=fieldValue + '\n'.join(noteList), inline=False)

        punishments = ''
        punsCol = mclient.bowser.puns.find({'user': user.id, 'type': {'$ne': 'note'}})
        if not punsCol.count():
            punishments = '__*No punishments on record*__'

        else:
            puns = 0
            activeStrikes = 0
            totalStrikes = 0
            activeMute = None
            for pun in punsCol.sort('timestamp', pymongo.DESCENDING):
                if pun['type'] == 'strike':
                    totalStrikes += pun['strike_count']
                    activeStrikes += pun['active_strike_count']

                elif pun['type'] == 'destrike':
                    totalStrikes -= pun['strike_count']

                elif pun['type'] == 'mute':
                    if pun['active']:
                        activeMute = pun['expiry']

                if puns >= 5:
                    continue

                puns += 1
                stamp = f'<t:{int(pun["timestamp"])}:f>'
                punType = config.punStrs[pun['type']]
                if pun['type'] in ['clear', 'unmute', 'unban', 'unblacklist', 'destrike']:
                    if pun['type'] == 'destrike':
                        punType = f'Removed {pun["strike_count"]} Strike{"s" if pun["strike_count"] > 1 else ""}'

                    punishments += f'> {config.removeTick} {stamp} **{punType}**\n'

                else:
                    if pun['type'] == 'strike':
                        punType = f'{pun["strike_count"]} Strike{"s" if pun["strike_count"] > 1 else ""}'

                    punishments += f'> {config.addTick} {stamp} **{punType}**\n'

            punishments = (
                f'Showing {puns}/{punsCol.count()} punishment entries. '
                f'For a full history including responsible moderator, active status, and more use `/history {user.id}`'
                f'\n\n{punishments}'
            )

            if activeMute:
                embed.description += f'\n**User is currently muted until <t:{activeMute}:f>**'

            if totalStrikes:
                embed.description += f'\nUser currently has {activeStrikes} active strike{"s" if activeStrikes != 1 else ""} ({totalStrikes} in total)'

        embed.add_field(name='Punishments', value=punishments, inline=False)
        return await interaction.followup.send(embed=embed, view=self.SuggestHistCommand(interaction))

    class SuggestHistCommand(discord.ui.View):
        def __init__(self, interaction: discord.Interaction):
            super().__init__(timeout=600.0)
            self.INTERACTION = interaction

        @discord.ui.button(label='Pull User History', style=discord.ButtonStyle.primary)
        async def pull_history(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Pull user ID from embed author of the interaction message, then pass to history to interact
            userid = int(re.search(r'\| (\d+)', interaction.message.embeds[0].author.name).group(1))
            user = interaction.client.get_user(userid)
            if not user:
                user = interaction.client.fetch_user(userid)

            self.INTERACTION = interaction
            ChatCog = ChatControl(bot=interaction.client)
            await ChatCog._pull_history(interaction, user)

        async def on_timeout(self):
            await self.INTERACTION.edit_original_response(view=None)

    @app_commands.command(name='history', description='Get detailed information on a user\'s infraction history')
    @app_commands.describe(user='The user you wish to get infractions for. If left blank, get your own history')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _history(self, interaction: discord.Interaction, user: typing.Optional[discord.User]):
        if not user:
            user = interaction.user
        return await self._pull_history(interaction, user)

    async def _pull_history(self, interaction: discord.Interaction, user: discord.User):
        if user is None:
            user = interaction.user

        if (
            interaction.guild.get_role(config.moderator) not in interaction.user.roles
            and interaction.guild.get_role(config.eh) not in interaction.user.roles
        ):
            await interaction.response.defer(ephemeral=True)
            self_check = True

            #  If they are not mod and not running on themselves, they do not have permssion.
            if user != interaction.user:
                return await interaction.followup.send(
                    f'{config.redTick} You do not have permission to run this command on other users'
                )

        else:
            await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
            self_check = False

        db = mclient.bowser.puns
        puns = db.find({'user': user.id, 'type': {'$ne': 'note'}}) if self_check else db.find({'user': user.id})

        deictic_language = {
            'no_punishments': ('User has no punishments on record.', 'You have no available punishments on record.'),
            'single_inf': (
                'There is **1** infraction record for this user:',
                'You have **1** available infraction record:',
            ),
            'multiple_infs': (
                'There are **{}** infraction records for this user:',
                'You have **{}** available infraction records:',
            ),
            'total_strikes': (
                'User currently has **{}** active strikes (**{}** in total.)\n',
                'You currently have **{}** active strikes (**{}** in total.)\n',
            ),
        }

        punNames = {
            'strike': '{} Strike{}',
            'destrike': 'Removed {} Strike{}',
            'tier1': 'T1 Warn',
            'tier2': 'T2 Warn',
            'tier3': 'T3 Warn',
            'clear': 'Warn Clear',
            'mute': 'Mute',
            'unmute': 'Unmute',
            'kick': 'Kick',
            'ban': 'Ban',
            'unban': 'Unban',
            'blacklist': 'Blacklist ({})',
            'unblacklist': 'Unblacklist ({})',
            'appealdeny': 'Denied ban appeal ({})',
            'note': 'User note',
        }

        if puns.count() == 0:
            desc = deictic_language["no_punishments"][self_check]
        elif puns.count() == 1:
            desc = deictic_language['single_inf'][self_check]
        else:
            desc = deictic_language['multiple_infs'][self_check].format(puns.count())

        fields = []
        activeStrikes = 0
        totalStrikes = 0
        for pun in puns.sort('timestamp', pymongo.DESCENDING):
            datestamp = f'<t:{int(pun["timestamp"])}:f>'
            moderator = interaction.guild.get_member(pun['moderator'])
            if not moderator:
                moderator = await self.bot.fetch_user(pun['moderator'])

            if pun['type'] == 'strike':
                activeStrikes += pun['active_strike_count']
                totalStrikes += pun['strike_count']
                inf = punNames[pun['type']].format(pun['strike_count'], "s" if pun['strike_count'] > 1 else "")

            elif pun['type'] == 'destrike':
                totalStrikes -= pun['strike_count']
                inf = punNames[pun['type']].format(pun['strike_count'], "s" if pun['strike_count'] > 1 else "")

            elif pun['type'] in ['blacklist', 'unblacklist']:
                inf = punNames[pun['type']].format(pun['context'])

            elif pun['type'] == 'appealdeny':
                inf = punNames[pun['type']].format(
                    f'until <t:{int(pun["expiry"])}:D>' if pun["expiry"] else "permanently"
                )

            else:
                inf = punNames[pun['type']]

            value = f'**Moderator:** {moderator}\n**Details:** [{inf}] {pun["reason"]}'

            if len(value) > 1024:  # This shouldn't happen, but it does -- split long values up
                strings = []
                offsets = list(range(0, len(value), 1018))  # 1024 - 6 = 1018

                for i, o in enumerate(offsets):
                    segment = value[o : (o + 1018)]

                    if i == 0:  # First segment
                        segment = f'{segment}...'
                    elif i == len(offsets) - 1:  # Last segment
                        segment = f'...{segment}'
                    else:
                        segment = f'...{segment}...'

                    strings.append(segment)

                for i, string in enumerate(strings):
                    fields.append({'name': f'{datestamp} ({i+1}/{len(strings)})', 'value': string})

            else:
                fields.append({'name': datestamp, 'value': value})

        if totalStrikes:
            desc = deictic_language['total_strikes'][self_check].format(activeStrikes, totalStrikes) + desc

        author = {'name': f'{user} | {user.id}', 'icon_url': user.display_avatar.url}
        view = tools.PaginatedEmbed(
            interaction=interaction,
            fields=fields,
            title='Infraction History',
            description=desc,
            color=0x18EE1C,
            author=author,
        )

        await interaction.edit_original_response(content='Here is the requested user history:', view=view)

    @app_commands.command(
        name='echoreply', description='Use the bot to reply to a message. Must provide either text, attachment, or both'
    )
    @app_commands.describe(
        message='The message link that you want to reply to',
        text='The text to use in the reply',
        attachment='An attachment to reply with',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _reply(
        self,
        interaction: discord.Interaction,
        message: str,
        text: typing.Optional[str],
        attachment: typing.Optional[discord.Attachment],
    ):
        if not text and not attachment:
            # User didn't provide anything
            await interaction.response.send_message(
                f'{config.redTick} No attributes were provided. You must provide either `text`, `attachment`, or both in the command'
            )

        await interaction.response.defer()
        elements = message.split('/')
        try:
            message = (
                await self.bot.get_guild(int(elements[4])).get_channel(int(elements[5])).fetch_message(int(elements[6]))
            )

        except (discord.NotFound, discord.Forbidden):
            return await interaction.followup.send(f'{config.redTick} The provided message link to reply to is invalid')

        files = []
        if attachment:
            data = io.BytesIO()
            await attachment.save(data)
            files.append(discord.File(data, attachment.filename))
        await message.reply(text, files=files)

        return await interaction.followup.send('Done')

    @app_commands.command(
        name='echo', description='Use the bot to send a message. Must provide either text, attachment, or both'
    )
    @app_commands.describe(
        channel='The channel to send a message in',
        text='The text to use in the reply',
        attachment='An attachment to reply with',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _echo(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        text: typing.Optional[str],
        attachment: typing.Optional[discord.Attachment],
    ):
        if not text and not attachment:
            # User didn't provide anything
            await interaction.response.send_message(
                f'{config.redTick} No attributes were provided. You must provide either `text`, `attachment`, or both in the command'
            )

        await interaction.response.defer()
        files = []
        if attachment:
            data = io.BytesIO()
            await attachment.save(data)
            files.append(discord.File(data, attachment.filename))
        await channel.send(text, files=files)

        return await interaction.followup.send('Done')

    @app_commands.command(name='roles', description='Get a list of all server roles and their IDs')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _roles(self, interaction):
        lines = []
        for role in reversed(interaction.guild.roles):
            lines.append(f'{role.name} ({role.id})')

        fields = tools.convert_list_to_fields(lines, codeblock=True)
        view = tools.PaginatedEmbed(
            interaction=interaction,
            fields=fields,
            title='List of roles in guild:',
            description='',
            page_character_limit=1500,
        )

        await interaction.edit_original_response(content='Here is the requested role list:', view=view)

    async def _tag_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> typing.List[app_commands.Choice[str]]:
        db = mclient.bowser.tags
        tags = db.find({'active': True})
        tagList = [tag['_id'] for tag in tags]
        if current == '':
            return [app_commands.Choice(name=t, value=t) for t in tagList[0:10]]

        extraction = process.extract(current.lower(), tagList, limit=10)
        return [app_commands.Choice(name=e[0], value=e[0]) for e in extraction] or []

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    class TagCommand(app_commands.Group):
        pass

    tag_group = TagCommand(name='tag', description='View and update text tags!')

    @tag_group.command(name='show', description='Show a stored tag')
    @app_commands.describe(query='The name of the tag you wish to pull up')
    @app_commands.autocomplete(query=_tag_autocomplete)
    async def _tag(self, interaction: discord.Interaction, query: str):
        db = mclient.bowser.tags

        query = query.lower()
        tag = db.find_one({'_id': query, 'active': True})

        if not tag:
            return await interaction.response.send_message(
                f'{config.redTick} A tag with that name does not exist', ephemeral=True
            )

        embed = discord.Embed(title=tag['_id'], description=tag['content'])
        embed.set_footer(text=f'Requested by {interaction.user}', icon_url=interaction.user.display_avatar.url)

        if 'img_main' in tag and tag['img_main']:
            embed.set_image(url=tag['img_main'])
        if 'img_thumb' in tag and tag['img_thumb']:
            embed.set_thumbnail(url=tag['img_thumb'])

        return await interaction.response.send_message(embed=embed)

    @tag_group.command(name='list', description='Get a list of all available tags')
    @app_commands.describe(search='A query to narrow down tags by')
    @app_commands.autocomplete(search=_tag_autocomplete)
    async def _tag_list(self, interaction: discord.Interaction, search: typing.Optional[str]):
        db = mclient.bowser.tags

        tagList = []
        for tag in db.find({'active': True}):
            description = '' if not 'desc' in tag else tag['desc']
            tagList.append({'name': tag['_id'].lower(), 'desc': description, 'content': tag['content']})

        tagList.sort(key=lambda x: x['name'])

        if not tagList:
            return await interaction.response.send_message(f'{config.redTick} This server has no tags', ephemeral=True)

        # If the command is being not being run in commands channel and not a mod or helpful user, use ephemeral
        if interaction.channel.id != config.commandsChannel:
            if not (
                interaction.guild.get_role(config.moderator) in interaction.user.roles
                or interaction.guild.get_role(config.helpfulUser) in interaction.user.roles
                or interaction.guild.get_role(config.trialHelpfulUser) in interaction.user.roles
            ):
                await interaction.response.defer(ephemeral=True)

            else:
                await interaction.response.defer()

        if search:
            embed_desc = f'Here is a list of tags you can access matching query `{search}`:\n*(Type `/tag show <name>` to request a tag)*'
        else:
            embed_desc = 'Here is a list of all tags you can access:\n*(Type `/tag show <name>` to request a tag or `/tag list <search>` to search tags)*'

        if search:
            search = search.lower()
            searchRanks = [0] * len(tagList)  # Init search rankings to 0

            # Search name first
            for i, name in enumerate([tag['name'] for tag in tagList]):
                if name.startswith(search):
                    searchRanks[i] = 1000
                elif search in name:
                    searchRanks[i] = 800

            # Search descriptions and tag bodies next
            for i, tag in enumerate(tagList):
                # add 15 * number of matches in desc
                searchRanks[i] += tag['desc'].lower().count(search) * 15
                # add 1 * number of matches in content
                searchRanks[i] += tag['content'].lower().count(search) * 1

            sort_joined_list = [(searchRanks[i], tagList[i]) for i in range(0, len(tagList))]
            sort_joined_list.sort(key=lambda e: e[0], reverse=True)  # Sort from highest rank to lowest

            matches = list(filter(lambda x: x[0] > 0, sort_joined_list))  # Filter to those with matches

            tagList = [x[1] for x in matches]  # Resolve back to tags

        if tagList:
            longest_name = len(max([tag['name'] for tag in tagList], key=len))
            lines = []

            for tag in tagList:
                name = tag['name'].ljust(longest_name)
                desc = '*No description*' if not tag['desc'] else tag['desc']

                lines.append(f'`{name}` {desc}')

        else:
            lines = ['*No results found*']

        fields = tools.convert_list_to_fields(lines, codeblock=False)
        view = tools.PaginatedEmbed(
            interaction=interaction, fields=fields, title='Tag List', description=embed_desc, page_character_limit=1500
        )

        await interaction.edit_original_response(content='Here is the requested list of tags:', view=view)

    class TagEdit(discord.ui.Modal):
        textbox = discord.ui.TextInput(
            label='What should be the text for this tag?',
            style=discord.TextStyle.long,
            required=True,
            min_length=1,
            max_length=4000,
        )

        def __init__(self, tag):
            super().__init__(title=f'Editing Tag: "{tag}"')
            self.tag = tag
            self.textbox.placeholder = 'Write some text! __Discord markdown is supported.__'

            self.db = mclient.bowser.tags
            self.doc = self.db.find_one({'_id': self.tag})
            if self.doc:
                self.textbox.default = self.doc['content']

        async def on_submit(self, interaction: discord.Interaction):
            if self.doc:
                self.db.update_one(
                    {'_id': self.tag},
                    {
                        '$push': {
                            'revisions': {
                                str(int(time.time())): {'content': self.doc['content'], 'user': interaction.user.id}
                            }
                        },
                        '$set': {'content': self.textbox.value, 'active': True},
                    },
                )

                msg = (
                    f'{config.greenTick} The **{self.tag}** tag has been ' + 'updated'
                    if self.doc['active']
                    else 'created'
                )
                await interaction.response.send_message(msg)

            else:
                self.db.insert_one({'_id': self.tag, 'content': self.textbox.value, 'revisions': [], 'active': True})
                return await interaction.response.send_message(
                    f'{config.greenTick} The **{self.tag}** tag has been created'
                )

    @tag_group.command(name='edit', description='Edit an existing tag, or create a new one with a given name')
    @app_commands.describe(name='Name of the tag to modify or create')
    @app_commands.autocomplete(name=_tag_autocomplete)
    @app_commands.checks.has_any_role(config.moderator, config.helpfulUser, config.trialHelpfulUser)
    async def _tag_create(self, interaction: discord.Interaction, name: str):
        if name in ['list', 'search', 'edit', 'delete', 'source', 'setdesc', 'setimg']:  # Name blacklist
            return await interaction.response.send_message(f'{config.redTick} You cannot use that name for a tag')

        modal = self.TagEdit(name.lower())
        return await interaction.response.send_modal(modal)

    @tag_group.command(name='delete', description='Delete an existing tag')
    @app_commands.describe(name='Name of the tag to delete')
    @app_commands.autocomplete(name=_tag_autocomplete)
    @app_commands.checks.has_any_role(config.moderator, config.helpfulUser, config.trialHelpfulUser)
    async def _tag_delete(self, interaction: discord.Interaction, name: str):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})
        if tag:
            view = tools.RiskyConfirmation(timeout=20)
            await interaction.response.send_message(
                f'This action will delete the tag "{name}", are you sure you want to proceed?', view=view
            )
            view.message = await interaction.original_response()
            await view.wait()

            if view.timedout:
                await view.message.edit(content='Deletion timed out. Rerun command to try again', view=view)

            if view.value:
                db.update_one({'_id': name}, {'$set': {'active': False}})
                await view.message.edit(content=f'{config.greenTick} The "{name}" tag has been deleted')

            else:
                await view.message.edit(content=f'Deletion of tag "{name}" canceled')

        else:
            return await interaction.response.send_message(f'{config.redTick} The tag "{name}" does not exist')

    @tag_group.command(name='description', description='Change the description flavor text of a tag')
    @app_commands.describe(
        name='Name of the tag which to update the description for',
        content='The new description for the tag. Leave blank to clear the existing description',
    )
    @app_commands.autocomplete(name=_tag_autocomplete)
    @app_commands.checks.has_any_role(config.moderator, config.helpfulUser, config.trialHelpfulUser)
    async def _tag_setdesc(self, interaction: discord.Interaction, name: str, content: typing.Optional[str] = ''):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})

        content = ' '.join(content.splitlines())

        if tag:
            db.update_one({'_id': tag['_id']}, {'$set': {'desc': content}})

            status = 'updated' if content else 'cleared'
            return await interaction.response.send_message(
                f'{config.greenTick} The **{name}** tag description has been {status}'
            )

        else:
            return await interaction.response.send_message(f'{config.redTick} The tag "{name}" does not exist')

    @tag_group.command(name='image', description='Change the active images displayed on tags')
    @app_commands.describe(
        name='The name of the tag which to update an image',
        option='Which image should be changed',
        url='The URL of the image to use. Leave blank to clear it',
    )
    @app_commands.autocomplete(name=_tag_autocomplete)
    @app_commands.checks.has_any_role(config.moderator, config.helpfulUser, config.trialHelpfulUser)
    async def _tag_setimg(
        self,
        interaction: discord.Interaction,
        name: str,
        option: typing.Literal['main', 'thumbnail'],
        url: typing.Optional[str] = '',
    ):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})

        IMG_TYPES = {
            'main': {'key': 'img_main', 'name': 'main'},
            'thumbnail': {'key': 'img_thumb', 'name': 'thumbnail'},
        }

        img_type = IMG_TYPES[option]

        url = ' '.join(url.splitlines())
        match = tools.linkRe.match(url)
        if url and (
            not match or match.span()[0] != 0
        ):  # If url argument does not match or does not begin with a valid url
            return await interaction.response.send_message(f'{config.redTick} An invalid url, `{url}`, was given')

        if tag:
            db.update_one({'_id': tag['_id']}, {'$set': {img_type['key']: url}})

            status = 'updated' if url else 'cleared'
            return await interaction.response.send_message(
                f'{config.greenTick} The **{name}** tag\'s {img_type["name"]} image has been {status}'
            )
        else:
            return await interaction.response.send_message(f'{config.redTick} The tag "{name}" does not exist')

    @tag_group.command(name='source', description='Retrieve the raw source of a tag for easier')
    @app_commands.describe(name='Name of the tag to retrieve the raw source')
    @app_commands.autocomplete(name=_tag_autocomplete)
    @app_commands.checks.has_any_role(config.moderator, config.helpfulUser, config.trialHelpfulUser)
    async def _tag_source(self, interaction: discord.Interaction, name: str):
        db = mclient.bowser.tags
        name = name.lower()
        tag = db.find_one({'_id': name})

        if tag:
            embed = discord.Embed(title=f'{name} source', description=f'```md\n{tag["content"]}\n```')

            description = '' if not 'desc' in tag else tag['desc']
            img_main = '' if not 'img_main' in tag else tag['img_main']
            img_thumb = '' if not 'img_thumb' in tag else tag['img_thumb']

            embed.add_field(
                name='Description', value='*No description*' if not description else description, inline=True
            )
            embed.add_field(name='Main Image', value='*No URL set*' if not img_main else img_main, inline=True)
            embed.add_field(name='Thumbnail Image', value='*No URL set*' if not img_thumb else img_thumb, inline=True)

            return await interaction.response.send_message(embed=embed)

        else:
            return await interaction.response.send_message(f'{config.redTick} The tag "{name}" does not exist')

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class BlacklistCommand(app_commands.Group):
        pass

    blacklist_group = BlacklistCommand(
        name='blacklist', description='Toggle permissions to allow or disallow a user to interact in some way'
    )

    async def _blacklist_execute(
        self,
        interaction: discord.Interaction,
        status: str,
        member: discord.Member,
        reason: str,
        context: str,
        feature: str,
    ):
        db = mclient.bowser.puns

        public_notify = False
        try:
            await member.send(tools.format_pundm(status.lower()[:-2], reason, interaction.user, feature))

        except (discord.Forbidden, AttributeError):  # User has DMs off, or cannot send to Obj
            public_notify = True

        if status.lower() == 'blacklisted':
            docID = await tools.issue_pun(
                member.id, interaction.user.id, 'blacklist', reason, context=context, public_notify=public_notify
            )

        else:
            db.find_one_and_update(
                {'user': member.id, 'type': 'blacklist', 'active': True, 'context': context},
                {'$set': {'active': False}},
            )
            docID = await tools.issue_pun(
                member.id,
                interaction.user.id,
                'unblacklist',
                reason,
                active=False,
                context=context,
                public_notify=public_notify,
            )

        await tools.send_modlog(
            self.bot,
            self.modLogs,
            status.lower()[:-2],
            docID,
            reason,
            user=member,
            moderator=interaction.user,
            extra_author=context,
            public=True,
        )

        await interaction.followup.send(f'{config.greenTick} {member} has been {status.lower()} from {feature}')

    @blacklist_group.command(
        name='feature', description='Toggle permissions to allow or disallow a user to interact in some way'
    )
    @app_commands.describe(
        member='The member you wish to toggle features on',
        feature='The unique feature to toggle access to',
        reason='The reason you are toggling the blacklist status for this user',
    )
    async def _blacklist_feature(
        self,
        interaction,
        member: discord.Member,
        feature: typing.Literal['modmail', 'reactions', 'attachments/embeds'],
        reason: app_commands.Range[str, 1, 990],
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        statusText = ''
        if feature == 'modmail':
            context = 'modmail'
            users = mclient.bowser.users
            dbUser = users.find_one({'_id': member.id})

            if dbUser['modmail']:
                users.update_one({'_id': member.id}, {'$set': {'modmail': False}})
                statusText = 'Blacklisted'

            else:
                users.update_one({'_id': member.id}, {'$set': {'modmail': True}})
                statusText = 'Unblacklisted'

        elif feature == 'reactions':
            context = 'reaction'
            reactionsRole = interaction.guild.get_role(config.noReactions)
            if reactionsRole in member.roles:  # Toggle role off
                await member.remove_roles(reactionsRole)
                statusText = 'Unblacklisted'

            else:  # Toggle role on
                await member.add_roles(reactionsRole)
                statusText = 'Blacklisted'

        elif feature == 'attachments/embeds':
            context = 'attachment/embed'
            noEmbeds = interaction.guild.get_role(config.noEmbeds)
            if noEmbeds in member.roles:  # Toggle role off
                await member.remove_roles(noEmbeds)
                statusText = 'Unblacklisted'

            else:  # Toggle role on
                await member.add_roles(noEmbeds)
                statusText = 'Blacklisted'

        await self._blacklist_execute(interaction, statusText, member, reason, context, feature)

    @blacklist_group.command(
        name='channel',
        description='Toggle permissions to allow or disallow a user to interact with a channel or category',
    )
    @app_commands.describe(
        member='The member you wish to toggle features on',
        channel='The channel or category to toggle access to',
        reason='The reason you are toggling the blacklist status for this user',
    )
    async def _blacklist_channel(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        channel: typing.Literal['suggestions', 'spoilers', 'server events'],
        reason: app_commands.Range[str, 1, 990],
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))

        channels = {
            'suggestions': (
                interaction.guild.get_role(config.noSuggestions),
                interaction.guild.get_channel(config.suggestions),
            ),
            'spoilers': (interaction.guild.get_role(config.noSpoilers), interaction.guild.get_channel(config.spoilers)),
            'server events': [interaction.guild.get_role(config.noEvents)],  # List for compat
        }
        statusText = ''

        if channel == 'server events':
            context = 'events'
            mention = 'events'

        else:
            context = channels[channel][1].name
            mention = channels[channel][1].mention + ' channel'

        if channels[channel][0] in member.roles:  # Toggle role off
            await member.remove_roles(channels[channel][0])
            statusText = 'Unblacklisted'

        else:  # Toggle role on
            await member.add_roles(channels[channel][0])
            statusText = 'Blacklisted'

        await self._blacklist_execute(interaction, statusText, member, reason, context, mention)


async def setup(bot):
    global serverLogs
    global modLogs

    serverLogs = bot.get_channel(config.logChannel)
    modLogs = bot.get_channel(config.modChannel)

    await bot.add_cog(ChatControl(bot))
    logging.info('[Extension] Utility module loaded')


async def teardown(bot):
    bot.remove_cog('ChatControl')
    logging.info('[Extension] Utility module unloaded')
