import asyncio
import copy
import logging
import time
import typing
from datetime import datetime, timezone

import config
import discord
import pymongo
from discord import app_commands
from discord.ext import commands, tasks

import tools


mclient = pymongo.MongoClient(config.mongoURI)


class Moderation(commands.Cog, name='Moderation Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.publicModLogs = self.bot.get_channel(config.publicModChannel)
        self.taskHandles = {}
        self.NS = self.bot.get_guild(config.nintendoswitch)
        self.roles = {'mute': self.NS.get_role(config.mute)}

        # Publish all unposted/pending public modlogs on cog load
        db = mclient.bowser.puns
        pendingLogs = db.find({'public': True, 'public_log_message': None, 'type': {'$ne': 'note'}})
        loop = bot.loop
        for log in pendingLogs:
            loop.create_task(tools.send_public_modlog(bot, log['_id'], self.publicModLogs))

        # Run expiration tasks
        userDB = mclient.bowser.users
        pendingPuns = db.find({'active': True, 'type': {'$in': ['strike', 'mute']}})
        twelveHr = 60 * 60 * 12
        trackedStrikes = []  # List of unique users
        for pun in pendingPuns:
            if pun['type'] == 'strike':
                if pun['user'] in trackedStrikes:
                    continue  # We don't want to create many tasks when we only remove one
                user = userDB.find_one({'_id': pun['user']})
                trackedStrikes.append(pun['user'])
                if user['strike_check'] > time.time():  # In the future
                    tryTime = (
                        twelveHr
                        if user['strike_check'] - time.time() > twelveHr
                        else user['strike_check'] - time.time()
                    )
                    self.schedule_task(tryTime, pun['_id'], config.nintendoswitch)

                else:  # In the past
                    self.schedule_task(0, pun['_id'], config.nintendoswitch)

            elif pun['type'] == 'mute':
                tryTime = twelveHr if pun['expiry'] - time.time() > twelveHr else pun['expiry'] - time.time()
                self.schedule_task(tryTime, pun['_id'], config.nintendoswitch)

    def cog_unload(self):
        for task in self.taskHandles.values():
            task.cancel()

    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    class GuildGroupCommand(app_commands.Group):
        pass

    @app_commands.command(name='hide', description='Hide and mark the reason of an infraction as sensitive')
    @app_commands.describe(uuid='The infraction UUID, found in the footer of the mod log message embeds')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _hide_modlog(self, interaction: discord.Interaction, uuid: str):
        db = mclient.bowser.puns
        doc = db.find_one({'_id': uuid})

        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if not doc:
            return await interaction.followup.send(f'{config.redTick} No infraction with that UUID exists')

        sensitive = True if not doc['sensitive'] else False  # Toggle sensitive value

        if not doc['public_log_message']:
            # Public log has not been posted yet
            db.update_one({'_id': uuid}, {'$set': {'sensitive': sensitive}})
            return await interaction.followup.send(
                f'{config.greenTick} Successfully {"" if sensitive else "un"}marked modlog as sensitive',
            )

        else:
            # public_mod_log has a set value, meaning the log has been posted. We need to edit both msg and db now
            try:
                channel = self.bot.get_channel(doc['public_log_channel'])
                message = await channel.fetch_message(doc['public_log_message'])

                if not channel:
                    raise ValueError

            except (ValueError, discord.NotFound, discord.Forbidden):
                return await interaction.followup.send(
                    f'{config.redTick} There was an issue toggling that log\'s sensitive status; the message may have been deleted or I do not have permission to view the public log channel',
                )

            embed = message.embeds[0]
            embedDict = embed.to_dict()
            newEmbedDict = copy.deepcopy(embedDict)
            listIndex = 0
            for field in embedDict['fields']:
                # We are working with the dict because some logs can have `reason` at different indexes and we should not assume index position
                if (
                    field['name'] == 'Reason'
                ):  # This is subject to a breaking change if `name` updated, but I'll take the risk
                    if sensitive:
                        newEmbedDict['fields'][listIndex][
                            'value'
                        ] = 'This action\'s reason has been marked sensitive by the moderation team and is hidden. See <#671003325495509012> for more information on why logs are marked sensitive'

                    else:
                        newEmbedDict['fields'][listIndex]['value'] = doc['reason']

                    break

                listIndex += 1

            assert (
                embedDict['fields'] != newEmbedDict['fields']
            )  # Will fail if message was unchanged, this is likely because of a breaking change upstream in the pun flow
            db.update_one({'_id': uuid}, {'$set': {'sensitive': sensitive}})
            newEmbed = discord.Embed.from_dict(newEmbedDict)
            await message.edit(embed=newEmbed)

        await interaction.followup.send(
            f'{config.greenTick} Successfully toggled the sensitive status for that infraction'
        )

    infraction_group = GuildGroupCommand(name='infraction', description='Tools to update an existing infraction')

    @infraction_group.command(
        name='reason', description='Change the given reason for an infraction and notify the user of the change'
    )
    @app_commands.describe(
        uuid='The infraction UUID, found in the footer of the mod log message embeds',
        reason='The new reason text for the infraction',
    )
    async def _infraction_reason(self, interaction, uuid: str, reason: str):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} The new reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        await self._infraction_editing(interaction, uuid, reason)

    @infraction_group.command(name='duration', description='Update when an active mute will expire')
    @app_commands.describe(
        uuid='The infraction UUID, found in the footer of the mod log message embeds',
        duration='The new formatted duration for this mute -- measured from now',
        reason='The reason you are updating this mute duration',
    )
    async def _infraction_duration(self, interaction: discord.Interaction, uuid: str, duration: str, reason: str):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        await self._infraction_editing(interaction, uuid, reason, duration)

    async def _infraction_editing(self, interaction: discord.Interaction, uuid: str, reason: str, duration: str = None):
        db = mclient.bowser.puns
        doc = db.find_one({'_id': uuid})
        if not doc:
            return await interaction.followup.send(f'{config.redTick} An invalid infraction id was provided')

        if not doc['active'] and duration:
            return await interaction.followup.send(
                f'{config.redTick} That infraction has already expired and the duration cannot be edited',
            )

        if duration and doc['type'] != 'mute':  # TODO: Should we support strikes in the future?
            return await interaction.followup.send(
                f'{config.redTick} Setting durations is not supported for {doc["type"]}'
            )

        user = await self.bot.fetch_user(doc['user'])
        try:
            member = await interaction.guild.fetch_member(doc['user'])

        except:
            member = None

        if duration:
            try:
                _duration = tools.resolve_duration(duration)
                stamp = _duration.timestamp()
                expireStr = f'<t:{int(stamp)}:f> (<t:{int(stamp)}:R>)'
                try:
                    if int(duration):
                        raise TypeError

                except ValueError:
                    pass

            except (KeyError, TypeError):
                return await interaction.followup.send(f'{config.redTick} Invalid duration passed')

            if stamp - time.time() < 60:  # Less than a minute
                return await interaction.followup.send(
                    f'{config.redTick} Cannot set the new duration to be less than one minute'
                )

            twelveHr = 60 * 60 * 12
            tryTime = twelveHr if stamp - time.time() > twelveHr else stamp - time.time()
            self.schedule_task(tryTime, uuid, config.nintendoswitch)

            if member:
                await member.edit(timed_out_until=_duration, reason='Mute duration modified by moderator')

            db.update_one({'_id': uuid}, {'$set': {'expiry': int(stamp)}})
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'duration-update',
                doc['_id'],
                reason,
                user=user,
                moderator=interaction.user,
                expires=expireStr,
                extra_author=doc['type'].capitalize(),
            )

        else:
            db.update_one({'_id': uuid}, {'$set': {'reason': reason}})
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'reason-update',
                doc['_id'],
                reason,
                user=user,
                moderator=interaction.user,
                extra_author=doc['type'].capitalize(),
                updated=doc['reason'],
            )

        if doc['public_log_message']:
            # This could be None if the edit was done before the log post duration has passed
            try:
                pubChannel = self.bot.get_channel(doc['public_log_channel'])
                pubMessage = await pubChannel.fetch_message(doc['public_log_message'])
                embed = pubMessage.embeds[0]
                embedDict = embed.to_dict()
                newEmbedDict = copy.deepcopy(embedDict)
                listIndex = 0
                for field in embedDict['fields']:
                    # We are working with the dict because some logs can have `reason` at different indexes and we should not assume index position
                    if duration and field['name'] == 'Expires':
                        # This is subject to a breaking change if `name` updated, but I'll take the risk
                        newEmbedDict['fields'][listIndex]['value'] = expireStr
                        break

                    elif not duration and field['name'] == 'Reason':
                        newEmbedDict['fields'][listIndex]['value'] = reason
                        break

                    listIndex += 1

                assert (
                    embedDict['fields'] != newEmbedDict['fields']
                )  # Will fail if message was unchanged, this is likely because of a breaking change upstream in the pun flow
                newEmbed = discord.Embed.from_dict(newEmbedDict)
                await pubMessage.edit(embed=newEmbed)

            except Exception as e:
                logging.error(f'[Moderation] _infraction_duration: {e}')

        error = ''
        try:
            member = await interaction.guild.fetch_member(doc['user'])
            if duration:
                await member.send(tools.format_pundm('duration-update', reason, details=(doc['type'], expireStr)))

            else:
                await member.send(
                    tools.format_pundm(
                        'reason-update',
                        reason,
                        details=(
                            doc['type'],
                            f'<t:{int(doc["timestamp"])}:f>',
                        ),
                    )
                )

        except (discord.NotFound, discord.Forbidden, AttributeError):
            error = '. I was not able to DM them about this action'

        await interaction.followup.send(
            f'{config.greenTick} The {doc["type"]} {"duration" if duration else "reason"} has been successfully updated for {user} ({user.id}){error}',
        )

    @infraction_group.command(name='remove', description='Permanently delete an infraction. Dev-only')
    @app_commands.describe(uuid='The infraction UUID, found in the footer of the mod log message embeds')
    async def _inf_revoke(self, interaction: discord.Interaction, uuid: str):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        db = mclient.bowser.puns
        doc = db.find_one_and_delete({'_id': uuid})
        if not doc:  # Delete did nothing if doc is None
            return await interaction.followup.send(f'{config.redTick} No matching infraction found')

        await interaction.followup.send(
            f'{config.greenTick} removed {uuid}: {doc["type"]} against {doc["user"]} by {doc["moderator"]}'
        )

    @app_commands.command(name='ban', description='Ban a user from the guild')
    @app_commands.describe(
        users='The user or users you wish to ban. Must be user ids separated by a space',
        reason='The reason for issuing the ban',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _banning(
        self,
        interaction: discord.Interaction,
        users: str,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Ban reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        banCount = 0
        failedBans = []
        couldNotDM = False

        users = users.split()
        for user in users:
            try:
                user = int(user)

            except ValueError:
                if len(users) == 1:
                    return await interaction.followup.send(
                        f'{config.redTick} An argument provided in users is invalid: `{user}`'
                    )
                else:
                    failedBans.append(user)
                    continue

            member = interaction.guild.get_member(user)
            userid = user
            username = userid if (type(member) is int) else str(member)

            # If not a user, manually contruct a user object
            user = discord.Object(id=userid) if (type(user) is int) else user

            if member:
                usr_role_pos = member.top_role.position

            else:
                usr_role_pos = -1

            if (usr_role_pos >= interaction.guild.me.top_role.position) or (
                usr_role_pos >= interaction.user.top_role.position
            ):
                if len(users) == 1:
                    return await interaction.followup.send(
                        f'{config.redTick} Insufficent permissions to ban {username}'
                    )
                else:
                    failedBans.append(str(userid))
                    continue

            try:
                await interaction.guild.fetch_ban(user)
                if len(users) == 1:
                    if interaction.user.id == self.bot.user.id:  # Non-command invoke, such as automod
                        # We could do custom exception types, but the whole "automod context" is already a hack anyway.
                        raise ValueError
                    else:
                        return await interaction.followup.send(f'{config.redTick} {username} is already banned')

                else:
                    # If a many-user ban, don't exit if a user is already banned
                    failedBans.append(str(userid))
                    continue

            except discord.NotFound:
                pass

            try:
                await user.send(
                    tools.format_pundm('ban', reason, interaction.user, auto=interaction.user.id == self.bot.user.id)
                )

            except (discord.Forbidden, AttributeError):
                couldNotDM = True
                pass

            member = discord.Object(id=userid) if not member else member

            try:
                await interaction.guild.ban(member, reason=f'Ban action performed by moderator', delete_message_days=3)

            except discord.NotFound:
                # User does not exist
                if len(users) == 1:
                    return await interaction.followup.send(f'{config.redTick} User {userid} does not exist')

                failedBans.append(str(userid))
                continue

            docID = await tools.issue_pun(userid, interaction.user.id, 'ban', reason=reason)
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'ban',
                docID,
                reason,
                username=username,
                userid=userid,
                moderator=interaction.user,
                public=True,
            )
            banCount += 1

        if interaction.user.id != self.bot.user.id:  # Command invoke, i.e. anything not automod
            if len(users) == 1:
                resp = f'{config.greenTick} {users[0]} has been successfully banned'
                if couldNotDM:
                    resp += '. I was not able to DM them about this action'

            else:
                resp = f'{config.greenTick} **{banCount}** users have been successfully banned'
                if failedBans:
                    resp += (
                        f'. Failed to ban **{len(failedBans)}** from the provided list:\n```{" ".join(failedBans)}```'
                    )

            return await interaction.followup.send(resp)

    @app_commands.command(name='unban', description='Unban a specified user from the server')
    @app_commands.describe(user='The user id to unban', reason='The reason for unbanning the user')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unbanning(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Unban reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        db = mclient.bowser.puns
        try:
            await interaction.guild.fetch_ban(user)

        except discord.NotFound:
            return await interaction.followup.send(f'{config.redTick} {user} is not currently banned')

        openAppeal = mclient.modmail.logs.find_one({'open': True, 'ban_appeal': True, 'recipient.id': user.id})
        if openAppeal:
            return await interaction.followup.send(
                f'{config.redTick} You cannot use the unban command on {user} while a ban appeal is in-progress. You can accept the appeal in <#{int(openAppeal["channel_id"])}> with `/appeal accept [reason]`',
            )

        db.find_one_and_update({'user': user.id, 'type': 'ban', 'active': True}, {'$set': {'active': False}})
        docID = await tools.issue_pun(user.id, interaction.user.id, 'unban', reason, active=False)
        await interaction.guild.unban(user, reason='Unban action performed by moderator')
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'unban',
            docID,
            reason,
            username=str(user),
            userid=user.id,
            moderator=interaction.user,
            public=True,
        )
        await interaction.followup.send(f'{config.greenTick} {user} has been unbanned')

    @app_commands.command(name='kick', description='Kick a user from the guild')
    @app_commands.describe(
        users='The user or users you wish to kick. Must be user ids', reason='The reason for issuing the kick. Optional'
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _kicking(
        self,
        interaction: discord.Interaction,
        users: str,
        reason: typing.Optional[str] = '-No reason specified-',
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Kick reason is too long, reduce it by at least {len(reason) - 990} characters',
            )
        if not users:
            return await interaction.followup.send(f'{config.redTick} An invalid user was provided')

        kickCount = 0
        failedKicks = []
        couldNotDM = False

        for user in users:
            try:
                user = int(user)

            except ValueError:
                return await interaction.followup.send(
                    f'{config.redTick} An argument provided in users is invalid: `{user}`'
                )

            member = interaction.guild.get_member(user)
            userid = user
            username = userid if (type(member) is int) else str(member)

            if not member:
                try:
                    member = await interaction.guild.fetch_member(userid)
                except discord.HTTPException:  # Member not in guild
                    if len(users) == 1:
                        return await interaction.followup.send(f'{config.redTick} {username} is not the server')

                    else:
                        # If a many-user kick, don't exit if a user is already gone
                        failedKicks.append(str(userid))
                        continue

            usr_role_pos = member.top_role.position

            if (usr_role_pos >= interaction.guild.me.top_role.position) or (
                usr_role_pos >= interaction.user.top_role.position
            ):
                if len(users) == 1:
                    return await interaction.followup.send(
                        f'{config.redTick} Insufficent permissions to kick {username}'
                    )
                else:
                    failedKicks.append(str(userid))
                    continue

            try:
                await user.send(tools.format_pundm('kick', reason, interaction.user))
            except (discord.Forbidden, AttributeError):
                couldNotDM = True
                pass

            try:
                await member.kick(reason='Kick action performed by moderator')
            except discord.Forbidden:
                failedKicks.append(str(userid))
                continue

            docID = await tools.issue_pun(member.id, interaction.user.id, 'kick', reason, active=False)
            await tools.send_modlog(
                self.bot, self.modLogs, 'kick', docID, reason, user=member, moderator=interaction.user, public=True
            )
            kickCount += 1

        if interaction.user.id != self.bot.user.id:  # Non-command invoke, such as automod
            if len(users) == 1:
                resp = f'{config.greenTick} {users[0]} has been successfully kicked'
                if couldNotDM:
                    resp += '. I was not able to DM them about this action'

            else:
                resp = f'{config.greenTick} **{kickCount}** users have been successfully kicked'
                if failedKicks:
                    resp += f'. Failed to kick **{len(failedKicks)}** from the provided list:\n```{" ".join(failedKicks)}```'

            return await interaction.followup.send(resp)

    @app_commands.command(
        name='mute',
        description='Timeout a user for a period of time and disallow sending messages or joining voice chat',
    )
    @app_commands.describe(
        member='The user you wish to timeout. Must be a user id',
        duration='The formatted duration the user should be timed out for',
        reason='The reason for issuing the mute. Optional',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _muting(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: typing.Optional[str] = '-No reason specified-',
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Mute reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        db = mclient.bowser.puns
        if db.find_one({'user': member.id, 'type': 'mute', 'active': True}):
            return await interaction.followup.send(f'{config.redTick} {member} ({member.id}) is already muted')

        try:
            _duration = tools.resolve_duration(duration)
            try:
                if int(duration):
                    raise TypeError

            except ValueError:
                pass

        except (KeyError, TypeError):
            return await interaction.followup.send(f'{config.redTick} Invalid duration passed')

        durDiff = (_duration - datetime.now(tz=timezone.utc)).total_seconds()
        if durDiff - 1 > 60 * 60 * 24 * 28:
            # Discord Timeouts cannot exceed 28 days, so we must check this
            return await interaction.followup.send(f'{config.redTick} Mutes cannot be longer than 28 days')

        try:
            member = await interaction.guild.fetch_member(member.id)
            usr_role_pos = member.top_role.position
        except:
            usr_role_pos = -1

        if (usr_role_pos >= interaction.guild.me.top_role.position) or (
            usr_role_pos >= interaction.user.top_role.position
        ):
            return await interaction.followup.send(f'{config.redTick} Insufficent permissions to mute {member.name}')

        await member.edit(timed_out_until=_duration, reason='Mute action performed by moderator')

        error = ""
        public_notify = False
        try:
            await member.send(
                tools.format_pundm('mute', reason, interaction.user, f'<t:{int(_duration.timestamp())}:R>')
            )

        except (discord.Forbidden, AttributeError):
            error = '. I was not able to DM them about this action'
            public_notify = True

        await interaction.followup.send(f'{config.greenTick} {member} ({member.id}) has been successfully muted{error}')

        docID = await tools.issue_pun(
            member.id, interaction.user.id, 'mute', reason, int(_duration.timestamp()), public_notify=public_notify
        )
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'mute',
            docID,
            reason,
            user=member,
            moderator=interaction.user,
            expires=f'<t:{int(_duration.timestamp())}:f> (<t:{int(_duration.timestamp())}:R>)',
            public=True,
        )

        twelveHr = 60 * 60 * 12
        expireTime = time.mktime(_duration.timetuple())
        tryTime = twelveHr if expireTime - time.time() > twelveHr else expireTime - time.time()
        self.schedule_task(tryTime, docID, interaction.guild.id)

    @app_commands.command(name='unmute', description='Unmute a user who is currently timed out')
    @app_commands.describe(member='The user you wish to unmute', reason='The reason for removing the mute. Optional')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unmuting(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: typing.Optional[str] = '-No reason specified-',
    ):  # TODO: Allow IDs to be unmuted (in the case of not being in the guild)
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Unmute reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        db = mclient.bowser.puns
        action = db.find_one_and_update(
            {'user': member.id, 'type': 'mute', 'active': True}, {'$set': {'active': False}}
        )
        if not action:
            return await interaction.followup.send(
                f'{config.redTick} Cannot unmute {member} ({member.id}), they are not currently muted'
            )

        await member.edit(timed_out_until=None, reason='Unmute action performed by moderator')

        error = ""
        public_notify = False
        try:
            await member.send(tools.format_pundm('unmute', reason, interaction.user))

        except (discord.Forbidden, AttributeError):
            error = '. I was not able to DM them about this action'
            public_notify = True

        await interaction.followup.send(
            f'{config.greenTick} {member} ({member.id}) has been successfully unmuted{error}'
        )

        docID = await tools.issue_pun(
            member.id,
            interaction.user.id,
            'unmute',
            reason,
            context=action['_id'],
            active=False,
            public_notify=public_notify,
        )
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'unmute',
            docID,
            reason,
            user=member,
            moderator=interaction.user,
            public=True,
        )

    @app_commands.command(name='note', description='Attach a private mod note to a user\'s account')
    @app_commands.describe(user='The user id you wish to attach a note to', content='The content of the note')
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _note(self, interaction: discord.Interaction, user: discord.User, content: str):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        userid = user if (type(user) is int) else user.id

        if len(content) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Note is too long, reduce it by at least {len(content) - 990} characters'
            )

        await tools.issue_pun(userid, interaction.user.id, 'note', content, active=False, public=False)

        return await interaction.followup.send(f'{config.greenTick} Note successfully added to {user} ({user.id})')

    @app_commands.command(name='strike', description='Issue 1 to 16 strikes to a user')
    @app_commands.describe(
        user='The user id you wish to strike',
        count='The number of strikes you want to issue. Must be between 1 and 16; total active strikes cannot exceed 16',
        reason='The reason for issuing the strike(s)',
        mode='Determines if strikes are being added or set to a level. Valid options: \'add\' or \'set\'. Defaults to add',
    )
    @app_commands.guilds(discord.Object(id=config.nintendoswitch))
    @app_commands.default_permissions(view_audit_log=True)
    @app_commands.checks.has_any_role(config.moderator, config.eh)
    async def _strike(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        count: int,
        reason: str,
        mode: typing.Literal['add', 'set'] = 'add',
    ):
        await interaction.response.defer(ephemeral=tools.mod_cmd_invoke_delete(interaction.channel))
        if count <= 0:
            return await interaction.followup.send(
                f'{config.redTick} You cannot issue less than one strike. If you need to reset this user\'s strikes to zero instead use `/strike set`',
            )

        elif count > 16:
            return await interaction.followup.send(
                f'{config.redTick} You cannot issue more than sixteen strikes to a user at once'
            )

        mode = mode.lower()
        if mode not in ['add', 'set']:
            # A typing.Literal should prevent this, but the case is handled should it fail
            return await interaction.followup.send(
                f'{config.redTick} The strike mode must be either \'add\' or \'set\''
            )

        if len(reason) > 990:
            return await interaction.followup.send(
                f'{config.redTick} Strike reason is too long, reduce it by at least {len(reason) - 990} characters',
            )

        punDB = mclient.bowser.puns
        userDB = mclient.bowser.users
        userDoc = userDB.find_one({'_id': user.id})
        if not userDoc:
            return await interaction.followup.send(
                f'{config.redTick} Unable strike user who has never joined the server'
            )

        activeStrikes = 0
        for pun in punDB.find({'user': user.id, 'type': 'strike', 'active': True}):
            activeStrikes += pun['active_strike_count']

        error = ""
        public_notify = False

        if mode == 'set':
            if activeStrikes == count:
                # Mod trying to set strikes to existing amount
                return await interaction.followup.send(
                    f'{config.redTick} That user already has {activeStrikes} active strikes'
                )

            if count > activeStrikes:
                # Mod is setting a higher amount, pretend this is a mode: add from here
                count = count - activeStrikes
                mode = 'add'

            elif count < activeStrikes:
                # Mod is setting lower amount, we need to remove strikes
                removedStrikes = activeStrikes - count
                diff = removedStrikes  # accumlator

                puns = punDB.find({'user': user.id, 'type': 'strike', 'active': True}).sort('timestamp', 1)
                for pun in puns:
                    if pun['active_strike_count'] - diff >= 0:
                        punDB.update_one(
                            {'_id': pun['_id']},
                            {
                                '$set': {
                                    'active_strike_count': pun['active_strike_count'] - diff,
                                    'active': pun['active_strike_count'] - diff > 0,
                                }
                            },
                        )
                        userDB.update_one(
                            {'_id': user.id}, {'$set': {'strike_check': time.time() + (60 * 60 * 24 * 7)}}
                        )
                        self.schedule_task(60 * 60 * 12, pun['_id'], interaction.guild.id)

                        # Logic to calculate the remaining (diff) strikes will simplify to 0
                        # new_diff = diff - removed_strikes
                        #          = diff - (old_strike_amount - new_strike_amount)
                        #          = diff - (old_strike_amount - (old_strike_amount - diff))
                        #          = diff - old_strike_amount + old_strike_amount - diff
                        #          = 0
                        diff = 0
                        break

                    elif pun['active_strike_count'] - diff < 0:
                        punDB.update_one({'_id': pun['_id']}, {'$set': {'active_strike_count': 0, 'active': False}})
                        diff -= pun['active_strike_count']

                if diff != 0:  # Something has gone horribly wrong
                    raise ValueError('Diff != 0 after full iteration')

                try:
                    await user.send(tools.format_pundm('destrike', reason, interaction.user, details=removedStrikes))
                except discord.Forbidden:
                    error = 'I was not able to DM them about this action'
                    public_notify = True

                docID = await tools.issue_pun(
                    user.id,
                    interaction.user.id,
                    'destrike',
                    reason=reason,
                    active=False,
                    strike_count=removedStrikes,
                    public_notify=public_notify,
                )
                await tools.send_modlog(
                    self.bot,
                    self.modLogs,
                    'destrike',
                    docID,
                    reason,
                    user=user,
                    moderator=interaction.user,
                    extra_author=(removedStrikes),
                    public=True,
                )

                await interaction.followup.send(
                    f'{config.greenTick} {user} ({user.id}) has had {removedStrikes} strikes removed, '
                    f'they now have {count} strike{"s" if count > 1 else ""} '
                    f'({activeStrikes} - {removedStrikes}) {error}',
                )

        if mode == 'add':
            # Separate statement because a strike set can resolve to an 'add' if the set would be additive
            activeStrikes += count
            if activeStrikes > 16:  # Max of 16 active strikes
                return await interaction.followup.send(
                    f'{config.redTick} Striking {count} time{"s" if count > 1 else ""} would exceed the maximum of 16 strikes. The amount being issued must be lowered by at least {activeStrikes - 16} or consider banning the user instead',
                )

            try:
                await user.send(tools.format_pundm('strike', reason, interaction.user, details=count))

            except discord.Forbidden:
                error = '. I was not able to DM them about this action'
                public_notify = True

            if activeStrikes == 16:
                error += '.\n:exclamation: You may want to consider a ban'

            docID = await tools.issue_pun(
                user.id,
                interaction.user.id,
                'strike',
                reason,
                strike_count=count,
                public=True,
                public_notify=public_notify,
            )

            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'strike',
                docID,
                reason,
                user=user,
                moderator=interaction.user,
                extra_author=count,
                public=True,
            )

            userDB.update_one({'_id': user.id}, {'$set': {'strike_check': time.time() + (60 * 60 * 24 * 7)}})  # 7 days
            self.schedule_task(60 * 60 * 12, docID, interaction.guild.id)

            await interaction.followup.send(
                f'{config.greenTick} {user} ({user.id}) has been successfully struck, they now have '
                f'{activeStrikes} strike{"s" if activeStrikes > 1 else ""} ({activeStrikes-count} + {count}){error}',
            )

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if not ctx.command:
            return

        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(
                f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(
                f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`',
                delete_after=15,
            )

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command', delete_after=15)

        else:
            await ctx.send(
                f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.',
                delete_after=15,
            )
            raise error

    def schedule_task(self, tryTime: int, _id: str, guild_id: int):
        if _id in self.taskHandles.keys():
            self.taskHandles[_id].cancel()

        self.taskHandles[_id] = self.bot.loop.call_later(
            tryTime, asyncio.create_task, self.expire_actions(_id, guild_id)
        )

    async def expire_actions(self, _id, guild):
        db = mclient.bowser.puns
        doc = db.find_one({'_id': _id})
        if not doc:
            logging.error(f'[Moderation] Expiry failed. Doc {_id} does not exist!')
            return

        # Lets do a sanity check.
        if not doc['active']:
            logging.debug(f'[Moderation] Expiry failed. Doc {_id} is not active but was scheduled to expire!')
            return

        twelveHr = 60 * 60 * 12
        if doc['type'] == 'strike':
            userDB = mclient.bowser.users
            user = userDB.find_one({'_id': doc['user']})
            try:
                if user['strike_check'] > time.time():
                    # To prevent drift we recall every 12 hours. Schedule for 12hr or expiry time, whichever is sooner
                    retryTime = (
                        twelveHr
                        if user['strike_check'] - time.time() > twelveHr
                        else user['strike_check'] - time.time()
                    )
                    self.schedule_task(retryTime, _id, guild)
                    return

            except (
                KeyError
            ):  # This is a rare edge case, but if a pun is manually created the user may not have the flag yet. More a dev handler than not
                logging.error(
                    f'[Moderation] Expiry failed. Could not get strike_check from db.users resolving for pun {_id}, was it manually added?'
                )

            # Start logic
            if doc['active_strike_count'] - 1 == 0:
                db.update_one({'_id': doc['_id']}, {'$set': {'active': False}, '$inc': {'active_strike_count': -1}})
                strikes = [
                    x for x in db.find({'user': doc['user'], 'type': 'strike', 'active': True}).sort('timestamp', 1)
                ]
                if not strikes:  # Last active strike expired, no additional
                    del self.taskHandles[_id]
                    return

                self.schedule_task(60 * 60 * 12, strikes[0]['_id'], guild)

            elif doc['active_strike_count'] > 0:
                db.update_one({'_id': doc['_id']}, {'$inc': {'active_strike_count': -1}})
                self.schedule_task(60 * 60 * 12, doc['_id'], guild)

            else:
                logging.warning(
                    f'[Moderation] Expiry failed. Doc {_id} had a negative active strike count and was skipped'
                )
                del self.taskHandles[_id]
                return

            userDB.update_one({'_id': doc['user']}, {'$set': {'strike_check': time.time() + 60 * 60 * 24 * 7}})

        elif doc['type'] == 'mute' and doc['expiry']:  # A mute that has an expiry
            # To prevent drift we recall every 12 hours. Schedule for 12hr or expiry time, whichever is sooner
            # This could also fail if the expiry time is changed by a mod
            if doc['expiry'] > time.time():
                retryTime = twelveHr if doc['expiry'] - time.time() > twelveHr else doc['expiry'] - time.time()
                self.schedule_task(retryTime, _id, guild)
                return

            punGuild = self.bot.get_guild(guild)
            try:
                member = await punGuild.fetch_member(doc['user'])

            except discord.NotFound:
                # User has left the server after the mute was issued. Lets just move on and let on_member_join handle on return
                return

            except discord.HTTPException:
                # Issue with API, lets just try again later in 30 seconds
                self.schedule_task(30, _id, guild)
                return

            public_notify = False
            try:
                await member.send(tools.format_pundm('unmute', 'Mute expired', None, auto=True))

            except discord.Forbidden:  # User has DMs off
                public_notify = True

            newPun = db.find_one_and_update({'_id': doc['_id']}, {'$set': {'active': False}})
            docID = await tools.issue_pun(
                doc['user'],
                self.bot.user.id,
                'unmute',
                'Mute expired',
                active=False,
                context=doc['_id'],
                public_notify=public_notify,
            )

            if not newPun:  # There is near zero reason this would ever hit, but in case...
                logging.error(
                    f'[Moderation] Expiry failed. Database failed to update user on pun expiration of {doc["_id"]}'
                )

            await member.edit(timed_out_until=None, reason='Automatic: Mute has expired')

            del self.taskHandles[_id]
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'unmute',
                docID,
                'Mute expired',
                user=member,
                moderator=self.bot.user,
                public=True,
            )


async def setup(bot):
    await bot.add_cog(Moderation(bot))
    logging.info('[Extension] Moderation module loaded')


async def teardown(bot):
    await bot.remove_cog('Moderation')
    logging.info('[Extension] Moderation module unloaded')
