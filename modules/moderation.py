import asyncio
import logging
import datetime
import time
import typing
import re

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

class Moderation(commands.Cog, name='Moderation Commands'):
    def __init__(self, bot):
        self.bot = bot
        self.serverLogs = self.bot.get_channel(config.logChannel)
        self.modLogs = self.bot.get_channel(config.modChannel)

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

            embed = discord.Embed(color=discord.Color(0xD0021B), timestamp=datetime.datetime.utcnow())
            embed.set_author(name=f'Ban | {username} ({user.id})')
            embed.add_field(name='User', value=f'<@{userid}>', inline=True)
            embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
            embed.add_field(name='Reason', value=reason)

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
            embed.set_footer(text=docID)
            await self.modLogs.send(embed=embed)
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

        db.find_one_and_update({'user': user, 'type': 'ban', 'active': True}, {'$set':{
            'active': False
        }})
        docID = await utils.issue_pun(user, ctx.author.id, 'unban', reason, active=False)
        await ctx.guild.unban(userObj, reason='Unban action performed by moderator')

        embed = discord.Embed(color=0x4A90E2, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Unban | {user}')
        embed.set_footer(text=docID)
        embed.add_field(name='User', value=f'<@{user}>', inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        await self.modLogs.send(embed=embed)
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
            await member.send(utils.format_pundm('kick', reason, {ctx.author}))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass
        await member.kick(reason='Kick action performed by moderator')

        embed = discord.Embed(color=0xD18407, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Kick | {member} ({member.id})')
        embed.set_footer(text=docID)
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        await self.modLogs.send(embed=embed)
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
        try:
            await member.send(utils.format_pundm('mute', reason, ctx.author, utils.humanize_duration(_duration)))
        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        embed = discord.Embed(color=0xB4A6EF, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Mute | {member} ({member.id})')
        embed.set_footer(text=docID)
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Expires', value=f'{_duration.strftime("%B %d, %Y %H:%M:%S UTC")} ({utils.humanize_duration(_duration)})', inline=True)
        embed.add_field(name='Reason', value=reason)

        await self.modLogs.send(embed=embed)
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
        try:
            await member.send(utils.format_pundm('unmute', reason, ctx.author))

        except (discord.Forbidden, AttributeError): # User has DMs off, or cannot send to Obj
            pass

        embed = discord.Embed(color=0x4A90E2, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Unmute | {member} ({member.id})')
        embed.set_footer(text=docID)
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        await self.modLogs.send(embed=embed)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully unmuted')

    @commands.group(name='warn', invoke_without_command=True)
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _warning(self, ctx, member: discord.Member, *, reason):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Warn reason is too long, reduce it by at least {len(reason) - 990} characters')
        db = mclient.bowser.puns
        warnLevel = 0
        tierLevel = {
            0: ctx.guild.get_role(config.warnTier1),
            1: ctx.guild.get_role(config.warnTier2),
            2: ctx.guild.get_role(config.warnTier3)
        }
        embedColor = {
            0: discord.Color(0xFFFA1C),
            1: discord.Color(0xFF9000),
            2: discord.Color(0xD0021B)
        }
        warnText = {
            0: 'First warning',
            1: 'Second warning',
            2: 'Third warning'
        }

        puns = db.find_one({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )
        _warnType = 'warn'
        if puns: # Active punishments, give tier 2/3
            if puns['type'] == 'tier3':
                return await ctx.send(f'{config.redTick} That user is already warn tier 3')

            _warnType = 'warnup'
            db.update_one({'_id': puns['_id']}, {'$set': {
                'active': False
            }})
            warnLevel = 2 if puns['type'] == 'tier2' else 1

        if _warnType == 'warn':
            embedWarnType = warnText[warnLevel]
    
        elif _warnType == 'warnup':
            embedWarnType = f'{warnText[warnLevel]} (was Tier {warnLevel})'

        embed = discord.Embed(color=embedColor[warnLevel], timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{embedWarnType} | {member} ({member.id})')
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        for role in member.roles:
            if role in [tierLevel[0], tierLevel[1], tierLevel[2]]:
                await member.remove_roles(role, reason='Warn action performed by moderator')

        await member.add_roles(tierLevel[warnLevel], reason='Warn action performed by moderator')
        docID = await utils.issue_pun(member.id, ctx.author.id, f'tier{warnLevel + 1}', reason, int(utils.resolve_duration('30d').timestamp()))
        try:
            await member.send(utils.format_pundm(_warnType, reason, ctx.author, f'tier {warnLevel + 1}'))
        except discord.Forbidden: # User has DMs off
            pass

        embed.set_footer(text=docID)
        await self.modLogs.send(embed=embed)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {str(member)} ({member.id}) has been successfully warned; they are now tier {warnLevel + 1}')

    @_warning.command(name='clear')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _warning_clear(self, ctx, member: discord.Member, *, reason):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Warn clear reason is too long, reduce it by at least {len(reason) - 990} characters')
        db = mclient.bowser.puns
        tierLevel = {
            1: ctx.guild.get_role(config.warnTier1),
            2: ctx.guild.get_role(config.warnTier2),
            3: ctx.guild.get_role(config.warnTier3)
        }
        puns = db.find({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )

        if not puns.count():
            return await ctx.send(f'{config.redTick} That user has no active warnings')

        for x in puns:
            db.update_one({'_id': x['_id']}, {'$set': {
                'active': False
            }})
            tierInt = int(x['type'][-1:])
            await member.remove_roles(tierLevel[tierInt])

        embed = discord.Embed(color=discord.Color(0x18EE1C), timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'Warnings cleared | {member} ({member.id})')
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        docID = await utils.issue_pun(member.id, ctx.author.id, 'clear', reason, active=False)
        embed.set_footer(text=docID)
        await self.modLogs.send(embed=embed)
        try:
            await member.send(utils.format_pundm('warnclear', reason, ctx.author))
        except discord.Forbidden: # User has DMs off
            pass

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} Warnings have been marked as inactive for {member} ({member.id})')

    @_warning.command(name='level')
    @commands.has_any_role(config.moderator, config.eh)
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def _warning_setlevel(self, ctx, member: discord.Member, tier: int, *, reason):
        if len(reason) > 990: return await ctx.send(f'{config.redTick} Warn reason is too long, reduce it by at least {len(reason) - 990} characters')
        if tier not in [1, 2, 3]:
            return await ctx.send(f'{config.redTick} Invalid tier number provided')
    
        db = mclient.bowser.puns
        tierLevel = {
            1: ctx.guild.get_role(config.warnTier1),
            2: ctx.guild.get_role(config.warnTier2),
            3: ctx.guild.get_role(config.warnTier3)
        }
        embedColor = {
            1: discord.Color(0xFFFA1C),
            2: discord.Color(0xFF9000),
            3: discord.Color(0xD0021B)
        }
        warnText = {
            1: 'First warning',
            2: 'Second warning',
            3: 'Third warning'
        }

        puns = db.find({'user': member.id, 'active': True, 'type': {
                    '$in': [
                        'tier1',
                        'tier2',
                        'tier3'
                    ]
                }
            }
        )
        _warnType = 'warn'
        oldTierInt = 0
        if puns:
            for x in puns:
                if oldTierInt == tier:
                    return await ctx.send(f'{config.redTick} User is already warned at that tier')

                db.update_one({'_id': x['_id']}, {'$set': {
                    'active': False
                }})
                oldTierInt = int(x['type'][-1:])

                await member.remove_roles(tierLevel[oldTierInt])
                if oldTierInt > tier:
                    _warnType = 'warndown'

                else:
                    _warnType = 'warnup'

        if _warnType == 'warn':
            embedWarnType = warnText[tier]

        elif _warnType in ['warnup', 'warndown']:
            embedWarnType = f'{warnText[tier]} (was Tier {oldTierInt})'
            
        embed = discord.Embed(color=embedColor[tier], timestamp=datetime.datetime.utcnow())
        embed.set_author(name=f'{embedWarnType} | {member} ({member.id})')
        embed.add_field(name='User', value=member.mention, inline=True)
        embed.add_field(name='Moderator', value=f'{ctx.author.mention}', inline=True)
        embed.add_field(name='Reason', value=reason)

        await member.add_roles(tierLevel[tier])
        docID = await utils.issue_pun(member.id, ctx.author.id, f'tier{tier}', reason, int(utils.resolve_duration('30d').timestamp()), context='level_set')
        embed.set_footer(text=docID)
        await self.modLogs.send(embed=embed)
        try:
            await member.send(utils.format_pundm(_warnType, reason, ctx.author, f'tier {tier}'))
        except discord.Forbidden: # User has DMs off
            pass

        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        await ctx.send(f'{config.greenTick} {member} ({member.id}) has been successfully warned; they are now tier {tier}')

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

        resp = await ctx.send(embed=embed)
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

                    try:
                        await member.send(utils.format_pundm('warndown', 'A moderator has reviewed your warning', None, newTier, True))

                    except (discord.Forbidden, discord.HTTPException):
                        pass

                    embed = discord.Embed(color=0x18EE1C, timestamp=datetime.datetime.utcnow())
                    embed.set_author(name=f'Warning reduced | {member} ({member.id})')
                    embed.set_footer(text=docID)
                    embed.add_field(name='User', value=member.mention, inline=True)
                    embed.add_field(name='New tier', value=config.punStrs[newTier][:-8], inline=True) # Shave off "warning" str from const
                    embed.add_field(name='Moderator', value=ctx.author.mention, inline=False)
                    embed.add_field(name='Reason', value='Moderator decision to reduce level', inline=True)
                    await self.modLogs.send(embed=embed)
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
        await utils.issue_pun(user.id, ctx.author.id, 'note', content, active=False)
        if await utils.mod_cmd_invoke_delete(ctx.channel):
            return await ctx.message.delete()

        return await ctx.send(f'{config.greenTick} Note successfully added to {user} ({user.id})')

    @_banning.error
    @_unbanning.error
    @_kicking.error
    @_muting.error
    @_unmuting.error
    @_warning.error
    @_warning_clear.error
    @_warning_setlevel.error
    @_warning_review.error
    @_note.error
    async def mod_error(self, ctx, error):
        cmd_str = ctx.command.full_parent_name + ' ' + ctx.command.name if ctx.command.parent else ctx.command.name
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f'{config.redTick} Missing one or more required arguments. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        elif isinstance(error, commands.BadArgument):
            return await ctx.send(f'{config.redTick} One or more provided arguments are invalid. See `{ctx.prefix}help {cmd_str}`', delete_after=15)

        else:
            await ctx.send(f'{config.redTick} An unknown exception has occured, if this continues to happen contact the developer.', delete_after=15)
            raise error

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
                logging.warning(f'[expiry_check] Moderator not in server for pun {pun["_id"]}, fetching instead')
                moderator = await self.bot.fetch_user(pun['moderator'])

            if pun['type'] == 'mute' and pun['expiry']: # A mute that has an expiry, for member in currently in guild
                if int(time.time()) < pun['expiry']: continue # Has not expired yet

                newPun = db.find_one_and_update({'_id': pun['_id']}, {'$set': {
                    'active': False
                }})
                docID = await utils.issue_pun(member.id, self.bot.user.id, 'unmute', 'auto', active=False, context=pun['_id'])

                if not newPun: # There is near zero reason this would ever hit, but in case...
                    logging.error(f'[expiry_check] Database failed to update user on pun expiration of {pun["_id"]}')
                    continue

                await member.remove_roles(self.roles[pun['type']])
                try:
                     await member.send(utils.format_pundm('unmute', 'Mute expired', None, auto=True))

                except discord.Forbidden: # User has DMs off
                    pass

                embed = discord.Embed(color=0x4A90E2, timestamp=datetime.datetime.utcnow())
                embed.set_author(name=f'Unmute | {member} ({member.id})')
                embed.set_footer(text=docID)
                embed.add_field(name='User', value=f'<@{member.id}>', inline=True)
                embed.add_field(name='Moderator', value='Automatic', inline=True)
                embed.add_field(name='Reason', value='Mute expired', inline=True)

                await self.modLogs.send(embed=embed)

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
                statusInfo = mclient.bowser.stats.find({'type': 'status', 'author': member.id, 'status': {'$ne': 'offline'}}).sort([('timestamp', -1)]).limit(1)
                if not statusInfo.count():
                    description = f"A warning for <@{pun['user']}> was issued over **30 days ago** ({issueDate}) and is now due for moderator review. This can either be __postponed__ to be re-reviewed at a later date or __reduced__ to the tier directly below (removed in the case of tier 1).\n\n**Infraction ID:** __{pun['_id']}__\n\n*I was unable to find the last active date for this user*"

                else:
                    lastStatus = statusInfo[0]
                    statusDate = datetime.datetime.utcfromtimestamp(lastStatus['timestamp']).strftime('%B %d, %Y at %H:%M UTC')
                    description = f"A warning for <@{pun['user']}> was issued over **30 days ago** ({issueDate}) and is now due for moderator review. This can either be __postponed__ to be re-reviewed at a later date or __reduced__ to the tier directly below (removed in the case of tier 1).\n\n**Infraction ID:** __{pun['_id']}__\n\nI last saw this user **{lastStatus['status']}** on **{statusDate}**"

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
