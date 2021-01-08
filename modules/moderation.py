import asyncio
import logging
import datetime
import time
import re
import copy
import typing

import pymongo
import discord
from discord.ext import commands, tasks

import config
import utils

mclient = pymongo.MongoClient(
	config.mongoHost,
	username=config.mongoUser,
	password=config.mongoPass
)

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

        if not 1 <= arg <= 16:
            raise commands.BadArgument

        return arg

class Moderation(commands.Cog, name='Moderation Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.publicModLogs = self.bot.get_channel(config.publicModChannel)
        self.taskHandles = []

        # Publish all unposted/pending public modlogs on cog load
        db = mclient.bowser.puns
        pendingLogs = db.find({'public': True, 'public_log_message': None, 'type': {'$ne': 'note'}})
        loop = bot.loop
        for log in pendingLogs:
            if log['type'] == 'mute':
                expires = utils.humanize_duration(datetime.datetime.utcfromtimestamp(log['expiry']))

            else:
                expires = None

            loop.create_task(utils.send_public_modlog(bot, log['_id'], self.publicModLogs, expires))

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

        sensitive = True if not doc['sensitive'] else False # Toggle sensitive value

        if not doc['public_log_message']:
            # Public log has not been posted yet
            db.update_one({'_id': uuid}, {'$set': {
                'sensitive': sensitive
            }})
            return await ctx.send(f'{config.greenTick} Successfully {"" if sensitive else "un"}marked modlog as sensitive')

        else:
            # public_mod_log has a set value, meaning the log has been posted. We need to edit both msg and db now
            try:
                channel = self.bot.get_channel(doc['public_log_channel'])
                message = await channel.fetch_message(doc['public_log_message'])

                if not channel: raise ValueError

            except (ValueError, discord.NotFound, discord.Forbidden):
                return await ctx.send(f'{config.redTick} There was an issue toggling that log\'s sensitive status; the message may have been deleted or I do not have permission to view the channel')

            embed = message.embeds[0]
            embedDict = embed.to_dict()
            print(embedDict['fields'])
            newEmbedDict = copy.deepcopy(embedDict)
            listIndex = 0
            for field in embedDict['fields']:
                # We are working with the dict because some logs can have `reason` at different indexes and we should not assume index position
                if field['name'] == 'Reason': # This is subject to a breaking change if `name` updated, but I'll take the risk
                    if sensitive:
                        newEmbedDict['fields'][listIndex]['value'] = 'This action\'s reason has been marked sensitive by the moderation team and is hidden. See <#671003325495509012> for more information on why logs are marked sensitive'

                    else:
                        newEmbedDict['fields'][listIndex]['value'] = doc['reason']

                    break

                listIndex += 1
            print(embedDict['fields'][listIndex]['value'])
            print(newEmbedDict['fields'][listIndex]['value'])
            assert embedDict['fields'] != newEmbedDict['fields'] # Will fail if message was unchanged, this is likely because of a breaking change upstream in the pun flow
            db.update_one({'_id': uuid}, {'$set': {
                'sensitive': sensitive
            }})
            newEmbed = discord.Embed.from_dict(newEmbedDict)
            await message.edit(embed=newEmbed)

        await ctx.send(f'{config.greenTick} Successfully toggled the sensitive status for that infraction')

    @commands.command(name='ban', aliases=['banid', 'forceban'])
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _banning(self, ctx, users: commands.Greedy[ResolveUser], *, reason='-No reason specified-'):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Ban reason is too long, reduce it by at least {len(reason) - 990} characters')
        if not users: return await ctx.send(f'{config.redTick} An invalid user was provided')
        banCount = 0
        failedBans = 0
        for user in users:
            userid = user if (type(user) is int) else user.id

            username = userid if (type(user) is int) else f'{str(user)}'
            user = discord.Object(id=userid) if (type(user) is int) else user # If not a user, manually contruct a user object
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
                await user.send(utils.format_pundm('ban', reason, ctx.author))
            except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
                pass

            try:
                await ctx.guild.ban(user, reason=f'Ban action performed by moderator', delete_message_days=3)

            except discord.NotFound:
                # User does not exist
                if len(users) == 1:
                    return await ctx.send(f'{config.redTick} User {userid} does not exist')

                failedBans += 1
                continue

            docID = await utils.issue_pun(userid, ctx.author.id, 'ban', reason=reason)
            await utils.send_modlog(self.bot, self.modLogs, 'ban', docID, reason, username=username, userid=userid, moderator=ctx.author, public=True)
            banCount += 1

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        if len(users) == 1:
            await ctx.send(f'{config.greenTick} {users[0]} has been successfully banned')

        else:
            resp = f'{config.greenTick} **{banCount}** users have been successfully banned'
            if failedBans: resp += f'. Failed to ban **{failedBans}** from the provided list'
            return await ctx.send(resp)

    @commands.command(name='unban')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unbanning(self, ctx, user: int, *, reason='-No reason specified-'):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Unban reason is too long, reduce it by at least {len(reason) - 990} characters')
        db = mclient.bowser.puns
        userObj = discord.Object(id=user)
        try:
            await ctx.guild.fetch_ban(userObj)

        except discord.NotFound:
            return await ctx.send(f'{config.redTick} {user} is not currently banned')

        openAppeal = mclient.modmail.logs.find_one({'open': True, 'ban_appeal': True, 'recipient.id': str(user)})
        if openAppeal:
            return await ctx.send(f'{config.redTick} You cannot use the unban command on {user} while a ban appeal is in-progress. You can accept the appeal in <#{int(openAppeal["channel_id"])}> with `!appeal accept [reason]`')

        db.find_one_and_update({'user': user, 'type': 'ban', 'active': True}, {'$set':{
            'active': False
        }})
        docID = await utils.issue_pun(user, ctx.author.id, 'unban', reason, active=False)
        await ctx.guild.unban(userObj, reason='Unban action performed by moderator')
        await utils.send_modlog(self.bot, self.modLogs, 'unban', docID, reason, username=str(user), userid=user, moderator=ctx.author, public=True)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {user} has been unbanned')

    @commands.command(name='kick')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _kicking(self, ctx, member: discord.Member, *, reason='-No reason specified-'):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Kick reason is too long, reduce it by at least {len(reason) - 990} characters')
        docID = await utils.issue_pun(member.id, ctx.author.id, 'kick', reason, active=False)
        try:
            await member.send(utils.format_pundm('kick', reason, ctx.author))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass
        await member.kick(reason='Kick action performed by moderator')
        await utils.send_modlog(self.bot, self.modLogs, 'kick', docID, reason, user=member, moderator=ctx.author, public=True)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully kicked')

    @commands.command(name='mute')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _muting(self, ctx, member: discord.Member, duration, *, reason='-No reason specified-'):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Mute reason is too long, reduce it by at least {len(reason) - 990} characters')
        db = mclient.bowser.puns
        if db.find_one({'user': member.id, 'type': 'mute', 'active': True}):
            return await ctx.send(f'{config.redTick} {member} ({member.id}) is already muted')

        muteRole = ctx.guild.get_role(config.mute)
        try:
            _duration = utils.resolve_duration(duration)
            if int(duration):
                raise TypeError

        except (KeyError, TypeError):
            return await ctx.send(f'{config.redTick} Invalid duration passed')

        except ValueError:
            pass

        docID = await utils.issue_pun(member.id, ctx.author.id, 'mute', reason, int(_duration.timestamp()))
        await member.add_roles(muteRole, reason='Mute action performed by moderator')
        await utils.send_modlog(self.bot, self.modLogs, 'mute', docID, reason, user=member, moderator=ctx.author, expires=f'{_duration.strftime("%B %d, %Y %H:%M:%S UTC")} ({utils.humanize_duration(_duration)})', public=True)
        try:
            await member.send(utils.format_pundm('mute', reason, ctx.author, utils.humanize_duration(_duration)))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully muted')

    @commands.command(name='unmute')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _unmuting(self, ctx, member: discord.Member, *, reason='-No reason specified-'): # TODO: Allow IDs to be unmuted (in the case of not being in the guild)
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Unmute reason is too long, reduce it by at least {len(reason) - 990} characters')
        db = mclient.bowser.puns
        muteRole = ctx.guild.get_role(config.mute)
        action = db.find_one_and_update({'user': member.id, 'type': 'mute', 'active': True}, {'$set':{
            'active': False
        }})
        if not action:
            return await ctx.send(f'{config.redTick} Cannot unmute {member} ({member.id}), they are not currently muted')

        docID = await utils.issue_pun(member.id, ctx.author.id, 'unmute', reason, context=action['_id'], active=False)
        await member.remove_roles(muteRole, reason='Unmute action performed by moderator')
        await utils.send_modlog(self.bot, self.modLogs, 'unmute', docID, reason, user=member, moderator=ctx.author, public=True)
        try:
            await member.send(utils.format_pundm('unmute', reason, ctx.author))

        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully unmuted')

    @commands.group(name='warn', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning(self, ctx):
        await ctx.send(f':warning: Warns are depreciated. Please use the strike system instead (`!help strike`); if you need to manually review an warning, you may still use the `!warn review user` command')

    @_warning.command(name='review')
    @commands.has_any_role(config.moderator, config.eh)
    async def _warning_review(self, ctx, member: discord.Member):
        db = mclient.bowser.puns
        warnPun = db.find_one({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )

        if not warnPun:
            return await ctx.send(f'{config.redTick} No warnings are currently active for {str(member)} ({member.id})')

        issueTime = datetime.datetime.utcfromtimestamp(warnPun['timestamp']).strftime('%B %d, %Y %H:%M:%S UTC')

        embed = discord.Embed(title="Warning review", colour=discord.Color(0xea4345), description="To change the status of the warning, react with the following emoji. You must react to make a choice.\n\n:track_next: Re-review in 30 days\n:fast_forward: Re-review in 14 days\n:arrow_forward: Re-review in 7 days\n:small_red_triangle_down: Reduce warn tier (or remove if tier1)\n:octagonal_sign: Make warning permanent", timestamp=utils.resolve_duration('15m'))
        embed.set_thumbnail(url=member.avatar_url)
        embed.set_author(name=f"{member} ({member.id})")
        embed.set_footer(text="This message will expire in 15 minutes")
        embed.add_field(name="Warning details", value=f'**Type:** {config.punStrs[warnPun["type"]]}\n**Issued by:** <@{warnPun["moderator"]}>\n**Issued at:** {issueTime}\n**Reason**: {warnPun["reason"]}')

        resp = await ctx.send(':warning: Warns are depreciated, please move to the strike system :warning:', embed=embed)
        await resp.add_reaction(config.nextTrack) # track_next
        await resp.add_reaction(config.fastForward) # fast_forward
        await resp.add_reaction(config.playButton) # arrow_forward
        await resp.add_reaction(config.downTriangle) # small_red_triangle_down
        await resp.add_reaction(config.stopSign) # octagonal_sign

        metaReactions = {
            config.nextTrack: 0,
            config.fastForward: 0,
            config.playButton: 0,
            config.downTriangle: 0,
            config.stopSign: 0
        }
        tierLevel = {
            'tier1': ctx.guild.get_role(config.warnTier1),
            'tier2': ctx.guild.get_role(config.warnTier2),
            'tier3': ctx.guild.get_role(config.warnTier3)
        }
        renew = False
        perm = False

        def check(reaction, user):
            if user.bot:
                return False
            #print(reaction.emoji)

            if ctx.guild.get_role(config.moderator) in user.roles and str(reaction.emoji) in [config.nextTrack, config.fastForward, config.playButton, config.downTriangle, config.stopSign]:
                metaReactions[str(reaction.emoji)] += 1
                #print(metaReactions)
                if metaReactions[str(reaction.emoji)] >= 1:
                    return True

            else:
                return False

        try:
            reaction = await self.bot.wait_for('reaction_add', timeout=900.0, check=check)
            #print(reaction[0])
            emoji = str(reaction[0])

            if emoji == config.nextTrack:
                renew = utils.resolve_duration('30d')

            elif emoji == config.fastForward:
                renew = utils.resolve_duration('2w')

            elif emoji == config.playButton:
                renew = utils.resolve_duration('1w')

            elif emoji == config.stopSign:
                renew = True
                perm = True

            if not renew:
                if warnPun['type'] == 'tier1':
                    await member.remove_roles(tierLevel[warnPun['type']])
                    db.update_one({'_id': warnPun['_id']}, {'$set': {'active': False}})
                    try:
                        await member.send(utils.format_pundm('warnclear', 'A moderator has reviewed your warning', None, auto=True))

                    except (discord.Forbidden, discord.HTTPException):
                        pass

                    # Because this does not generate a pun document, this will not be pushed to the pub modlog due to strike overhaul soon
                    embed = discord.Embed(color=0x18EE1C, timestamp=datetime.datetime.utcnow())
                    embed.set_author(name=f'Warning reduced | {member} ({member.id})')
                    embed.set_footer(text=warnPun['_id'])
                    embed.add_field(name='User', value=member.mention, inline=True)
                    embed.add_field(name='New tier', value='\*No longer under a warning*', inline=True) # pylint: disable=anomalous-backslash-in-string
                    embed.add_field(name='Moderator', value=ctx.author.mention, inline=False)
                    embed.add_field(name='Reason', value='Moderator decision to reduce level', inline=True)
                    await self.modLogs.send(embed=embed)
                    await resp.delete()
                    return await ctx.send(f'{config.greenTick} Warning review complete for {member} ({member.id}). Will be reduced one tier')

                else:
                    await member.remove_roles(tierLevel[warnPun['type']])
                    newTier = 'tier1' if warnPun['type'] == 'tier2' else 'tier2' # If tier2, make it tier1 else tier3 make it tier2
                    await member.add_roles(tierLevel[newTier])

                    db.update_one({'_id': warnPun['_id']}, {'$set': {'active': False}}) # Mark old warn as inactive and resubmit new warn tier
                    convertStr = f'(T{int(newTier[-1]) + 1}->T{newTier[-1]}) ' # Example return: "(T3->T2) "
                    docID = await utils.issue_pun(member.id, ctx.author.id, newTier, convertStr + warnPun['reason'], int(utils.resolve_duration('30d').timestamp()), context='vote')
                    await utils.send_modlog(self.bot, self.modLogs, newTier, docID, 'Warning tier decayed', user=member, moderator=ctx.author, extra_author=convertStr[1:-2], public=True)

                    try:
                        await member.send(utils.format_pundm('warndown', 'A moderator has reviewed your warning', None, newTier, True))

                    except (discord.Forbidden, discord.HTTPException):
                        pass

                    await resp.delete()
                    return await ctx.send(f'{config.greenTick} Warning review complete for {str(member)} ({member.id}). Will be reduced one tier')

            elif renew and perm:
                db.update_one({'_id': warnPun['_id']}, {'$set': {'expiry': None}})

                embed = discord.Embed(color=0xD0021B, timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Warning made permanent | {member} ({member.id})')
                embed.set_footer(text=warnPun['_id'])
                embed.add_field(name='User', value=member.mention, inline=True)
                embed.add_field(name='Reason', value='Moderator decision to make warning permanent')
                await self.modLogs.send(embed=embed)
                await resp.delete()
                return await ctx.send(f'{config.greenTick} Warning review complete for {str(member)} ({member.id}). Will be made permanent; no further warning review reminders will be sent.')

            else:
                db.update_one({'_id': warnPun['_id']}, {'$set': {'expiry': int(renew.timestamp())}})
                await resp.delete()
                return await ctx.send(f'{config.greenTick} Warning review complete for {str(member)} ({member.id}). Will be delayed {utils.humanize_duration(renew)}')

        except asyncio.TimeoutError:
            await resp.delete()
            return await ctx.send(content=f'{config.redTick} Command timed out. Rerun to continue.')

    @commands.has_any_role(config.moderator, config.eh)
    @commands.command(name='note')
    async def _note(self, ctx, user: discord.User, *, content):
        if len(content) > 900: return await ctx.send(f'{config.redTick} Note is too long, reduce it by at least {len(content) - 990} characters')
        await utils.issue_pun(user.id, ctx.author.id, 'note', content, active=False, public=False)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        return await ctx.send(f'{config.greenTick} Note successfully added to {user} ({user.id})')

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='strike', invoke_without_command=True)
    async def _strike(self, ctx, count: typing.Optional[StrikeRange] = 1, *, reason):
        pass

    @commands.is_owner()
    @commands.command()
    async def migratewarns(self, ctx):
        """
        Temporary command for debugging and migration. To be removed upon full migration completion.
        """
        db = mclient.bowser.puns
        userDB = mclient.bowser.users
        loop = self.bot.loop
        punCount = db.count_documents({'active': True, 'type': {'$in': ['tier1', 'tier2', 'tier3']}})
        if not punCount > 0:
            return await ctx.send('nothing to do!')

        failures = 0
        for doc in db.find({'active': True, 'type': {'$in': ['tier1', 'tier2', 'tier3']}}):
            strikeCount = int(doc['type'][-1:]) * 4
            try:
                member = await ctx.guild.fetch_member(doc['user'])

            except discord.NotFound:
                userDB.update_one({'_id': doc['user']}, {'$set': {'migrate_unnotified': True}}) # Set flag for on_member_join to instruct of new system should they return
                continue # TODO: handle this in core

            db.update_one({'_id': doc['_id']}, {'$set': {'active': False}})
            docID = await utils.issue_pun(doc['user'], self.bot.user.id, 'strike', f'[Migrated] {doc["reason"]}', strike_count=strikeCount, context='strike-migration', public=False)
            self.taskHandles.append(loop.call_later(5, asyncio.create_task, self.expire_actions(docID, ctx.guild.id)))
            userDB.update_one({'_id': member.id}, {'$set': {'strike_check': time.time() + (60 * 60 * 24 * 7)}}) # Setting the next expiry check time

            explanation = """Hello there **{}**,\nI am letting you know of a change in status for your active level {} warning issued on {}.\n\nThe **/r/NintendoSwitch** Discord server is moving to a strike-based system for infractions. Here is what you need to know:\n\* Your warning level will be converted to **{}** strikes.\n\* __Your strikes will decay at the same rate as warnings previously did__. Each warning tier is the same as four strikes with one strike decaying per-week instead of one warn level per four weeks.\n\* You will no longer have any permission restrictions you previously had with this warning. Moderators will instead restrict features as needed to enforce the rules on a case-by-case basis.\n\nStrikes will allow the moderation team to weigh rule-breaking behavior better and serve as a reminder to users who may need to review our rules. Please feel free to send a modmail to @Parakarry (<@{}>) if you have any questions or concerns.""".format(
                str(member), # Username
                doc['type'][-1:], # Tier type
                datetime.datetime.utcfromtimestamp(doc['timestamp']).strftime('%B %d, %Y'), # Date of warn
                strikeCount, # How many strikes will replace tier,
                config.parakarry # Parakarry mention for DM
            )

            try:
                await member.send(explanation)

            except discord.Forbidden:
                failures += 1
                continue

            except discord.HTTPException as e:
                failures += 1
                logging.error(f'[Warn Migration] Failed to migrate {member.id}, {e}')
                continue

        await ctx.send(f'Completed action. Unable to notify {failures} users')

    @_banning.error
    @_unbanning.error
    @_kicking.error
    @_muting.error
    @_unmuting.error
    @_warning.error
    @_warning_review.error
    @_strike.error
    @_note.error
    @_hide_modlog.error
    async def mod_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.CheckFailure):
            return await ctx.send(f'{config.redTick} You do not have permission to run this command', delete_after=15)
            
        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.', delete_after=15)
            raise error

    async def expire_actions(self, _id, guild):
        db = mclient.bowser.puns
        doc = db.find_one({'_id': _id})
        if not doc:
            logging.error(f'[Moderation] Expiry failed. Doc {_id} does not exist!')
            return

        # Lets do a sanity check.
        if not doc['active']:
            logging.error(f'[Moderation] Expiry failed. Doc {_id} is not active but was scheduled to expire!')

        if doc['type'] == 'strike':
            userDB = mclient.bowser.users
            user = userDB.find_one({'_id': doc['user']})
            twelveHr = 60 * 60 * 12
            if user['strike_check'] > time.time() - 5: # To prevent drift we recall every 12 hours. Schedule for 12hr or expiry time, whichever is sooner. 5 seconds is for drift lienency
                retryTime = twelveHr if user['strike_check'] - time.time() > twelveHr else user['strike_check'] - time.time()
                self.taskHandles.append(self.bot.loop.call_later(retryTime, asyncio.create_task, self.expire_actions(_id, guild)))
                return

            # Start logic
            if doc['active_strike_count'] - 1 == 0:
                db.update_one({'_id': doc['_id']}, {'$set': {'active': False}, '$inc': {'active_strike_count': -1}})
                strikes = [x for x in db.find({'user': doc['user'], 'type': 'strike', 'active': True}).sort({'timestamp': 1})]
                if not strikes: # Last active strike expired, no additional
                    return

                self.taskHandles.append(self.bot.loop.call_later(60 * 60 * 12, asyncio.create_task, self.expire_actions(strikes[0]['_id'], guild)))

            elif doc['active_strike_count'] > 0:
                db.update_one({'_id': doc['_id']}, {'$inc': {'active_strike_count': -1}})
                self.taskHandles.append(self.bot.loop.call_later(60 * 60 * 12, asyncio.create_task, self.expire_actions(doc['_id'], guild)))

            else:
                logging.warning(f'[Moderation] Expiry failed. Doc {_id} had a negative active strike count and was skipped')
                return

            userDB.update_one({'_id': doc['user']}, {'$set': {'strike_check': time.time() + 60 * 60 * 24 * 7}})

class LoopTasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.NS = self.bot.get_guild(config.nintendoswitch)
        self.modLogs = self.bot.get_channel(config.modChannel)
        self.adminChannel = self.bot.get_channel(config.adminChannel)
        self.expiryWarnNotified = {}
        self.roles = {
            'tier1': self.NS.get_role(config.warnTier1),
            'tier2': self.NS.get_role(config.warnTier2),
            'tier3': self.NS.get_role(config.warnTier3),
            'mute': self.NS.get_role(config.mute)
        }
        self.expiry_check.start() #pylint: disable=no-member
        self.expiry_check.add_exception_type(discord.errors.DiscordServerError) #pylint: disable=no-member
        logging.info('[Cog] Moderation tasks cog loaded')

    def cog_unload(self):
        logging.info('[Cog] Attempting to stop task expiry_check...')
        self.expiry_check.stop() #pylint: disable=no-member
        logging.info('[Cog] Task expiry_check exited')
        logging.info('[Cog] Moderation tasks cog unloaded')

    @tasks.loop(seconds=30)
    async def expiry_check(self):
        logging.debug('[Moderation] Starting expiry check')
        db = mclient.bowser.puns
        activePuns = db.find({'active': True, 'expiry': {'$ne': None}})
        if not activePuns.count():
            #logging.info('[Moderation] No active puns to cycle through')
            return
        #print(f'{activePuns.count()}')

        warns = ['tier1', 'tier2', 'tier3']
        for pun in activePuns:
            await asyncio.sleep(0.01) # Give some breathing room to the rest of the thread as this is more long running
            #print('processing pun')
            try:
                member = await self.NS.fetch_member(pun['user'])

            except discord.NotFound: # User not in guild
                continue

            try:
                moderator = await self.NS.fetch_member(pun['moderator'])

            except:
                logging.debug(f'[expiry_check] Moderator not in server for pun {pun["_id"]}, fetching instead')
                moderator = await self.bot.fetch_user(pun['moderator'])

            if pun['type'] == 'mute' and pun['expiry']: # A mute that has an expiry, for member in currently in guild
                if int(time.time()) < pun['expiry']: continue # Has not expired yet

                newPun = db.find_one_and_update({'_id': pun['_id']}, {'$set': {
                    'active': False
                }})
                docID = await utils.issue_pun(member.id, self.bot.user.id, 'unmute', 'Mute expired', active=False, context=pun['_id'])

                if not newPun: # There is near zero reason this would ever hit, but in case...
                    logging.error(f'[expiry_check] Database failed to update user on pun expiration of {pun["_id"]}')
                    continue

                await member.remove_roles(self.roles[pun['type']])
                try:
                     await member.send(utils.format_pundm('unmute', 'Mute expired', None, auto=True))

                except discord.Forbidden: # User has DMs off
                    pass

                await utils.send_modlog(self.bot, self.modLogs, 'unmute', docID, 'Mute expired', user=member, moderator=self.bot.user, public=True)

            elif pun['type'] in warns and member:
                if int(time.time()) < (pun['expiry']):
                    #print('warn not review ready')
                    continue

                if pun['_id'] in self.expiryWarnNotified.keys():
                    if time.time() <= self.expiryWarnNotified[pun['_id']] + (60 * 60 * 24): # Only send one review message every 24 hours max
                        #print('warn already notified')
                        continue

                punsCol = db.find({'user': member.id})
                puns = 0
                punishments = ''
                for n in punsCol.sort('timestamp',pymongo.DESCENDING):
                    if puns >= 5:
                        break

                    puns += 1
                    stamp = datetime.datetime.utcfromtimestamp(n['timestamp']).strftime('%m/%d/%y %H:%M:%S UTC')
                    punType = config.punStrs[n['type']]
                    if n['type'] in ['clear', 'unmute', 'unban', 'unblacklist']:
                        punishments += f'- [{stamp}] {punType}\n'

                    else:
                        punishments += f'+ [{stamp}] {punType}\n'

                punishments = f'Showing {puns}/{punsCol.count()} punishment entries. ' \
                    f'For a full history, use `!history {member.id}`' \
                    f'\n```diff\n{punishments}```'
                issueDate = datetime.datetime.utcfromtimestamp(pun['timestamp']).strftime('%B %d, %Y')
                description = f"A warning for <@{pun['user']}> was issued over **30 days ago** ({issueDate}) and is now due for moderator review. This can either be __postponed__ to be re-reviewed at a later date or __reduced__ to the tier directly below (removed in the case of tier 1).\n\n**Infraction ID:** __{pun['_id']}__"

                embed = discord.Embed(title="Warning due for staff review", colour=discord.Color(0xddbe2d), description=description, timestamp=datetime.datetime.utcfromtimestamp(pun['timestamp']))
                embed.set_thumbnail(url=member.avatar_url)
                embed.set_author(name=f"{member} ({member.id})", icon_url=member.avatar_url)
                embed.add_field(name="Responsible moderator", value=f"{str(moderator)} ({moderator.id})", inline=True)
                embed.add_field(name="Reason", value=pun['reason'], inline=True)
                embed.add_field(name="Previous punishments", value=punishments, inline=False)
                embed.add_field(name="Making a decision", value=f"An action is required for this review. Please use the `!warn review {member.id}` command to proceed", inline=False)

                await self.adminChannel.send(content=":warning::alarm_clock:", embed=embed)
                self.expiryWarnNotified[pun['_id']] = time.time()

def setup(bot):
    bot.add_cog(Moderation(bot))
    bot.add_cog(LoopTasks(bot))
    logging.info('[Extension] Moderation module loaded')

def teardown(bot):
    bot.remove_cog('Moderation')
    bot.remove_cog('LoopTasks')
    logging.info('[Extension] Moderation module unloaded')
