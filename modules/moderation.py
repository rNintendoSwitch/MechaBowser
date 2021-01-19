import asyncio
import copy
import datetime
import logging
import re
import time
import typing

import config
import discord
import pymongo
from discord.ext import commands, tasks

import tools


mclient = pymongo.MongoClient(config.mongoHost, username=config.mongoUser, password=config.mongoPass)


class ResolveUser(commands.Converter):
    async def convert(self, ctx, argument):
        if not argument:
            raise commands.BadArgument

        try:
            userid = int(argument)

        except ValueError:
            mention = re.search(r'<@!?(\d+)>', argument)
            if not mention:
                raise commands.BadArgument

            userid = int(mention.group(1))

        try:
            member = ctx.guild.get_member(userid)
            user = await ctx.bot.fetch_user(argument) if not member else member
            return user

        except discord.NotFound:
            raise commands.BadArgument


class StrikeRange(commands.Converter):
    async def convert(self, ctx, argument):
        if not argument:
            raise commands.BadArgument

        try:
            arg = int(argument)

        except:
            raise commands.BadArgument

        if not 0 <= arg <= 16:
            raise commands.BadArgument

        return arg


class Moderation(commands.Cog, name='Moderation Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.publicModLogs = self.bot.get_channel(config.publicModChannel)
        self.taskHandles = []
        self.NS = self.bot.get_guild(config.nintendoswitch)
        self.roles = {'mute': self.NS.get_role(config.mute)}

        # Publish all unposted/pending public modlogs on cog load
        db = mclient.bowser.puns
        pendingLogs = db.find({'public': True, 'public_log_message': None, 'type': {'$ne': 'note'}})
        loop = bot.loop
        for log in pendingLogs:
            if log['type'] == 'mute':
                expires = tools.humanize_duration(datetime.datetime.utcfromtimestamp(log['expiry']))

            else:
                expires = None

            loop.create_task(tools.send_public_modlog(bot, log['_id'], self.publicModLogs, expires))

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
                    self.taskHandles.append(
                        self.bot.loop.call_later(
                            tryTime, asyncio.create_task, self.expire_actions(pun['_id'], config.nintendoswitch)
                        )
                    )

                else:  # In the past
                    self.taskHandles.append(
                        self.bot.loop.call_soon(
                            asyncio.create_task, self.expire_actions(pun['_id'], config.nintendoswitch)
                        )
                    )

            elif pun['type'] == 'mute':
                tryTime = twelveHr if pun['expiry'] - time.time() > twelveHr else pun['expiry'] - time.time()
                logging.info(f'using {tryTime} for mute')
                self.taskHandles.append(
                    self.bot.loop.call_later(
                        tryTime, asyncio.create_task, self.expire_actions(pun['_id'], config.nintendoswitch)
                    )
                )

    def cog_unload(self):
        for task in self.taskHandles:
            task.cancel()

    @commands.command(name='hide', aliases=['unhide'])
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _hide_modlog(self, ctx, uuid):
        db = mclient.bowser.puns
        doc = db.find_one({'_id': uuid})

        if not doc:
            return await ctx.send(f'{config.redTick} No punishment with that UUID exists')

        sensitive = True if not doc['sensitive'] else False  # Toggle sensitive value

        if not doc['public_log_message']:
            # Public log has not been posted yet
            db.update_one({'_id': uuid}, {'$set': {'sensitive': sensitive}})
            return await ctx.send(
                f'{config.greenTick} Successfully {"" if sensitive else "un"}marked modlog as sensitive'
            )

        else:
            # public_mod_log has a set value, meaning the log has been posted. We need to edit both msg and db now
            try:
                channel = self.bot.get_channel(doc['public_log_channel'])
                message = await channel.fetch_message(doc['public_log_message'])

                if not channel:
                    raise ValueError

            except (ValueError, discord.NotFound, discord.Forbidden):
                return await ctx.send(
                    f'{config.redTick} There was an issue toggling that log\'s sensitive status; the message may have been deleted or I do not have permission to view the channel'
                )

            embed = message.embeds[0]
            embedDict = embed.to_dict()
            print(embedDict['fields'])
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
            print(embedDict['fields'][listIndex]['value'])
            print(newEmbedDict['fields'][listIndex]['value'])
            assert (
                embedDict['fields'] != newEmbedDict['fields']
            )  # Will fail if message was unchanged, this is likely because of a breaking change upstream in the pun flow
            db.update_one({'_id': uuid}, {'$set': {'sensitive': sensitive}})
            newEmbed = discord.Embed.from_dict(newEmbedDict)
            await message.edit(embed=newEmbed)

        await ctx.send(f'{config.greenTick} Successfully toggled the sensitive status for that infraction')

    @commands.command(name='ban', aliases=['banid', 'forceban'])
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _banning(self, ctx, users: commands.Greedy[ResolveUser], *, reason='-No reason specified-'):
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Ban reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        if not users:
            return await ctx.send(f'{config.redTick} An invalid user was provided')
        banCount = 0
        failedBans = 0
        for user in users:
            userid = user if (type(user) is int) else user.id

            username = userid if (type(user) is int) else f'{str(user)}'
            user = (
                discord.Object(id=userid) if (type(user) is int) else user
            )  # If not a user, manually contruct a user object
            try:
                await ctx.guild.fetch_ban(user)
                if len(users) == 1:
                    return await ctx.send(f'{config.redTick} {username} is already banned')

                else:
                    # If a many-user ban, don't exit if a user is already banned
                    failedBans += 1
                    continue

            except discord.NotFound:
                pass

            try:
                await user.send(tools.format_pundm('ban', reason, ctx.author))

            except (discord.Forbidden, AttributeError):
                pass

            try:
                await ctx.guild.ban(user, reason=f'Ban action performed by moderator', delete_message_days=3)

            except discord.NotFound:
                # User does not exist
                if len(users) == 1:
                    return await ctx.send(f'{config.redTick} User {userid} does not exist')

                failedBans += 1
                continue

            docID = await tools.issue_pun(userid, ctx.author.id, 'ban', reason=reason)
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'ban',
                docID,
                reason,
                username=username,
                userid=userid,
                moderator=ctx.author,
                public=True,
            )
            banCount += 1

        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        if len(users) == 1:
            await ctx.send(f'{config.greenTick} {users[0]} has been successfully banned')

        else:
            resp = f'{config.greenTick} **{banCount}** users have been successfully banned'
            if failedBans:
                resp += f'. Failed to ban **{failedBans}** from the provided list'
            return await ctx.send(resp)

    @commands.command(name='unban')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unbanning(self, ctx, user: int, *, reason='-No reason specified-'):
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Unban reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        db = mclient.bowser.puns
        userObj = discord.Object(id=user)
        try:
            await ctx.guild.fetch_ban(userObj)

        except discord.NotFound:
            return await ctx.send(f'{config.redTick} {user} is not currently banned')

        openAppeal = mclient.modmail.logs.find_one({'open': True, 'ban_appeal': True, 'recipient.id': str(user)})
        if openAppeal:
            return await ctx.send(
                f'{config.redTick} You cannot use the unban command on {user} while a ban appeal is in-progress. You can accept the appeal in <#{int(openAppeal["channel_id"])}> with `!appeal accept [reason]`'
            )

        db.find_one_and_update({'user': user, 'type': 'ban', 'active': True}, {'$set': {'active': False}})
        docID = await tools.issue_pun(user, ctx.author.id, 'unban', reason, active=False)
        await ctx.guild.unban(userObj, reason='Unban action performed by moderator')
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'unban',
            docID,
            reason,
            username=str(user),
            userid=user,
            moderator=ctx.author,
            public=True,
        )
        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {user} has been unbanned')

    @commands.command(name='kick')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _kicking(self, ctx, member: discord.Member, *, reason='-No reason specified-'):
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Kick reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        docID = await tools.issue_pun(member.id, ctx.author.id, 'kick', reason, active=False)
        await tools.send_modlog(
            self.bot, self.modLogs, 'kick', docID, reason, user=member, moderator=ctx.author, public=True
        )
        try:
            await member.send(tools.format_pundm('kick', reason, ctx.author))

        except (discord.Forbidden, AttributeError):
            if not await tools.mod_cmd_invoke_delete(ctx.channel):
                await ctx.send(
                    f'{config.greenTick} {member} ({member.id}) has been successfully kicked. I was not able to DM them about this action'
                )

            await member.kick(reason='Kick action performed by moderator')
            return

        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully kicked')

    @commands.command(name='mute')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _muting(self, ctx, member: discord.Member, duration, *, reason='-No reason specified-'):
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Mute reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        db = mclient.bowser.puns
        if db.find_one({'user': member.id, 'type': 'mute', 'active': True}):
            return await ctx.send(f'{config.redTick} {member} ({member.id}) is already muted')

        muteRole = ctx.guild.get_role(config.mute)
        try:
            _duration = tools.resolve_duration(duration)
            try:
                if int(duration):
                    raise TypeError

            except ValueError:
                pass

        except (KeyError, TypeError):
            return await ctx.send(f'{config.redTick} Invalid duration passed')

        docID = await tools.issue_pun(member.id, ctx.author.id, 'mute', reason, int(_duration.timestamp()))
        await member.add_roles(muteRole, reason='Mute action performed by moderator')
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'mute',
            docID,
            reason,
            user=member,
            moderator=ctx.author,
            expires=f'{_duration.strftime("%B %d, %Y %H:%M:%S UTC")} ({tools.humanize_duration(_duration)})',
            public=True,
        )
        try:
            await member.send(tools.format_pundm('mute', reason, ctx.author, tools.humanize_duration(_duration)))

        except (discord.Forbidden, AttributeError):
            if not await tools.mod_cmd_invoke_delete(ctx.channel):
                await ctx.send(
                    f'{config.greenTick} {member} ({member.id}) has been successfully muted. I was not able to DM them about this action'
                )

        else:
            await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully muted')

        twelveHr = 60 * 60 * 12
        expireTime = time.mktime(_duration.timetuple())
        logging.info(f'using {expireTime}')
        tryTime = twelveHr if expireTime - time.time() > twelveHr else expireTime - time.time()
        self.taskHandles.append(
            self.bot.loop.call_later(tryTime, asyncio.create_task, self.expire_actions(docID, ctx.guild.id))
        )
        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

    @commands.command(name='unmute')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unmuting(
        self, ctx, member: discord.Member, *, reason='-No reason specified-'
    ):  # TODO: Allow IDs to be unmuted (in the case of not being in the guild)
        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Unmute reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        db = mclient.bowser.puns
        muteRole = ctx.guild.get_role(config.mute)
        action = db.find_one_and_update(
            {'user': member.id, 'type': 'mute', 'active': True}, {'$set': {'active': False}}
        )
        if not action:
            return await ctx.send(
                f'{config.redTick} Cannot unmute {member} ({member.id}), they are not currently muted'
            )

        docID = await tools.issue_pun(member.id, ctx.author.id, 'unmute', reason, context=action['_id'], active=False)
        await member.remove_roles(muteRole, reason='Unmute action performed by moderator')
        await tools.send_modlog(
            self.bot, self.modLogs, 'unmute', docID, reason, user=member, moderator=ctx.author, public=True
        )

        try:
            await member.send(tools.format_pundm('unmute', reason, ctx.author))

        except (discord.Forbidden, AttributeError):
            if not await tools.mod_cmd_invoke_delete(ctx.channel):
                await ctx.send(
                    f'{config.greenTick} {member} ({member.id}) has been successfully unmuted. I was not able to DM them about this action'
                )

            return

        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully unmuted')

    @commands.has_any_role(config.moderator, config.eh)
    @commands.command(name='note')
    async def _note(self, ctx, user: ResolveUser, *, content):
        userid = user if (type(user) is int) else user.id

        if len(content) > 900:
            return await ctx.send(
                f'{config.redTick} Note is too long, reduce it by at least {len(content) - 990} characters'
            )

        await tools.issue_pun(userid, ctx.author.id, 'note', content, active=False, public=False)
        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        return await ctx.send(f'{config.greenTick} Note successfully added to {user} ({user.id})')

    @commands.group(name='warn', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning(self, ctx):
        await ctx.send(':warning: Warns are depreciated. Please use the strike system instead (`!help strike`)')

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='strike', invoke_without_command=True)
    async def _strike(self, ctx, member: discord.Member, count: typing.Optional[StrikeRange] = 1, *, reason):
        if count == 0:
            return await ctx.send(
                f'{config.redTick} You cannot issue less than one strike. If you need to reset this user\'s strikes to zero instead use `{ctx.prefix}strike set`'
            )

        if len(reason) > 990:
            return await ctx.send(
                f'{config.redTick} Strike reason is too long, reduce it by at least {len(reason) - 990} characters'
            )
        punDB = mclient.bowser.puns
        userDB = mclient.bowser.users

        activeStrikes = 0
        for pun in punDB.find({'user': member.id, 'type': 'strike', 'active': True}):
            activeStrikes += pun['active_strike_count']

        activeStrikes = +count
        if activeStrikes > 16:  # Max of 16 active strikes
            return await ctx.send(
                f'{config.redTick} Striking {count} time{"s" if count > 1 else ""} would exceed the maximum of 16 strikes. The amount being issued must be lowered by at least {activeStrikes - 16} or consider banning the user instead'
            )

        docID = await tools.issue_pun(member.id, ctx.author.id, 'strike', reason, strike_count=count, public=True)
        userDB.update_one({'_id': member.id}, {'$set': {'strike_check': time.time() + (60 * 60 * 24 * 7)}})  # 7 days

        self.taskHandles.append(
            self.bot.loop.call_later(60 * 60 * 12, asyncio.create_task, self.expire_actions(docID, ctx.guild.id))
        )  # Check in 12 hours, prevents time drifting
        await tools.send_modlog(
            self.bot,
            self.modLogs,
            'strike',
            docID,
            reason,
            user=member,
            moderator=ctx.author,
            extra_author=count,
            public=True,
        )
        content = f'{config.greenTick} {member} ({member.id}) has been successfully struck, they now have {activeStrikes} strike{"s" if activeStrikes > 1 else ""}'
        try:
            await member.send(tools.format_pundm('strike', reason, ctx.author, details=count))

        except discord.Forbidden:
            if not await tools.mod_cmd_invoke_delete(ctx.channel):
                content += '. I was not able to DM them about this action'
                if activeStrikes == 16:
                    content += '.\n:exclamation: You may want to consider a ban'

                await ctx.send(content)

            return

        if await tools.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        if activeStrikes == 16:
            content += '.\n:exclamation: You may want to consider a ban'

        await ctx.send(content)

    @commands.has_any_role(config.moderator, config.eh)
    @_strike.command(name='set')
    async def _strike_set(self, ctx, member: discord.Member, count: StrikeRange, *, reason):
        punDB = mclient.bowser.puns
        activeStrikes = 0
        puns = punDB.find({'user': member.id, 'type': 'strike', 'active': True})
        for pun in puns:
            activeStrikes += pun['active_strike_count']

        if activeStrikes == count:
            return await ctx.send(f'{config.redTick} That user already has {activeStrikes} active strikes')

        elif (
            count > activeStrikes
        ):  # This is going to be a positive diff, lets just do the math and defer work to _strike()
            return await self._strike(ctx, member, count - activeStrikes, reason=reason)

        else:  # Negative diff, we will need to reduce our strikes
            diff = activeStrikes - count

            puns = punDB.find({'user': member.id, 'type': 'strike', 'active': True}).sort('timestamp', 1)
            for pun in puns:
                if pun['active_strike_count'] - diff >= 0:
                    userDB = mclient.bowser.users
                    punDB.update_one(
                        {'_id': pun['_id']},
                        {
                            '$set': {
                                'active_strike_count': pun['active_strike_count'] - diff,
                                'active': pun['active_strike_count'] - diff > 0,
                            }
                        },
                    )
                    userDB.update_one({'_id': member.id}, {'$set': {'strike_check': time.time() + (60 * 60 * 24 * 7)}})
                    self.taskHandles.append(
                        self.bot.loop.call_later(
                            60 * 60 * 12, asyncio.create_task, self.expire_actions(pun['_id'], ctx.guild.id)
                        )
                    )  # Check in 12 hours, prevents time drifting

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

            docID = await tools.issue_pun(
                member.id, ctx.author.id, 'destrike', reason=reason, active=False, strike_count=activeStrikes - count
            )
            await tools.send_modlog(
                self.bot,
                self.modLogs,
                'destrike',
                docID,
                reason,
                user=member,
                moderator=ctx.author,
                extra_author=(activeStrikes - count),
                public=True,
            )
            try:
                await member.send(tools.format_pundm('destrike', reason, ctx.author, details=activeStrikes - count))

            except discord.Forbidden:
                if not await tools.mod_cmd_invoke_delete(ctx.channel):
                    await ctx.send(
                        f'{config.greenTick} {activeStrikes - count} strikes for {member} ({member.id}) have been successfully removed. I was not able to DM them about this action'
                    )

                return

            if await tools.mod_cmd_invoke_delete(ctx.channel):
                return await ctx.message.delete()

            await ctx.send(
                f'{config.greenTick} {activeStrikes - count} strikes for {member} ({member.id}) have been successfully removed'
            )

    @commands.is_owner()
    @commands.group(name='inf', invoke_without_command=True)
    async def _inf(self, ctx):
        return

    @commands.is_owner()
    @_inf.command('remove')
    async def _inf_revoke(self, ctx, _id):
        db = mclient.bowser.puns
        doc = db.find_one_and_delete({'_id': _id})
        if not doc:  # Delete did nothing if doc is None
            return ctx.send(f'{config.redTick} No matching infraction found')

        await ctx.send(f'{config.greenTick} removed {_id}: {doc["type"]} against {doc["user"]} by {doc["moderator"]}')

    @_banning.error
    @_unbanning.error
    @_kicking.error
    @_strike.error
    @_strike_set.error
    @_muting.error
    @_unmuting.error
    @_warning.error
    @_strike.error
    @_note.error
    @_hide_modlog.error
    async def mod_error(self, ctx, error):
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
                    self.taskHandles.append(
                        self.bot.loop.call_later(retryTime, asyncio.create_task, self.expire_actions(_id, guild))
                    )
                    return

            except KeyError:  # This is a rare edge case, but if a pun is manually created the user may not have the flag yet. More a dev handler than not
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
                    return

                self.taskHandles.append(
                    self.bot.loop.call_later(
                        60 * 60 * 12, asyncio.create_task, self.expire_actions(strikes[0]['_id'], guild)
                    )
                )

            elif doc['active_strike_count'] > 0:
                db.update_one({'_id': doc['_id']}, {'$inc': {'active_strike_count': -1}})
                self.taskHandles.append(
                    self.bot.loop.call_later(60 * 60 * 12, asyncio.create_task, self.expire_actions(doc['_id'], guild))
                )

            else:
                logging.warning(
                    f'[Moderation] Expiry failed. Doc {_id} had a negative active strike count and was skipped'
                )
                return

            userDB.update_one({'_id': doc['user']}, {'$set': {'strike_check': time.time() + 60 * 60 * 24 * 7}})

        elif doc['type'] == 'mute' and doc['expiry']:  # A mute that has an expiry
            # To prevent drift we recall every 12 hours. Schedule for 12hr or expiry time, whichever is sooner
            if doc['expiry'] > time.time():
                retryTime = twelveHr if doc['expiry'] - time.time() > twelveHr else doc['expiry'] - time.time()
                self.taskHandles.append(
                    self.bot.loop.call_later(retryTime, asyncio.create_task, self.expire_actions(_id, guild))
                )
                return

            punGuild = self.bot.get_guild(guild)
            try:
                member = await punGuild.fetch_member(doc['user'])

            except discord.NotFound:
                # User has left the server after the mute was issued. Lets just move on and let on_member_join handle on return
                return

            except discord.HTTPException:
                # Issue with API, lets just try again later in 30 seconds
                self.taskHandles.append(
                    self.bot.loop.call_later(30, asyncio.create_task, self.expire_actions(_id, guild))
                )
                return

            newPun = db.find_one_and_update({'_id': doc['_id']}, {'$set': {'active': False}})
            docID = await tools.issue_pun(
                doc['user'], self.bot.user.id, 'unmute', 'Mute expired', active=False, context=doc['_id']
            )

            if not newPun:  # There is near zero reason this would ever hit, but in case...
                logging.error(
                    f'[Moderation] Expiry failed. Database failed to update user on pun expiration of {doc["_id"]}'
                )

            await member.remove_roles(self.roles[doc['type']])
            try:
                await member.send(tools.format_pundm('unmute', 'Mute expired', None, auto=True))

            except discord.Forbidden:  # User has DMs off
                pass

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


def setup(bot):
    bot.add_cog(Moderation(bot))
    logging.info('[Extension] Moderation module loaded')


def teardown(bot):
    bot.remove_cog('Moderation')
    logging.info('[Extension] Moderation module unloaded')
