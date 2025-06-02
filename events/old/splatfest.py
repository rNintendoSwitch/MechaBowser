import asyncio
import collections
import logging
import re

import config
import discord
from discord.ext import commands


class Splatfest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ACTIVE = False
        self.team1 = None
        self.team2 = None
        self.team3 = None

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='splatfest', invoke_without_command=True)
    async def _splatfest(self, ctx):
        def check(m):
            return m.author == ctx.author

        try:
            # Team 1 logic
            await ctx.send(
                'Welcome to Splatfest setup! You may respond "cancel" at any time to cancel setup. Now, lets get the teams down for this event -- what is the name of team 1?'
            )
            _team1 = await self.bot.wait_for('message', check=check, timeout=60.0)
            _team1 = _team1.content
            if _team1 == 'cancel':
                return await ctx.send('Canceled setup. Rerun command to try again')

            msg = await ctx.send(f'What emote represents team {_team1}?')
            while True:
                try:
                    _team1_emote = await self.bot.wait_for('message', check=check, timeout=60.0)
                    _team1_emote = _team1_emote.content
                    if _team1_emote == 'cancel':
                        return await ctx.send('Canceled setup. Rerun command to try again')
                    await msg.add_reaction(_team1_emote)
                    await msg.remove_reaction(_team1_emote, self.bot.user)
                    break

                except (discord.NotFound, TypeError, ValueError, discord.HTTPException):
                    await ctx.send(
                        f'{config.redTick} That is not a valid emoji, please send a valid unicode or custom emoji'
                    )

            await ctx.send(f'What role represents team {_team1}? (Please send the ID)')
            while True:
                try:
                    _role = await self.bot.wait_for('message', check=check, timeout=60.0)
                    _role = _role.content
                    if _role == 'cancel':
                        return await ctx.send('Canceled setup. Rerun command to try again')
                    _team1_role = ctx.guild.get_role(int(_role))
                    if not _team1_role:
                        raise ValueError
                    break

                except ValueError:
                    await ctx.send(f'{config.redTick} That is not a valid role, please send a valid role ID')

            # Team 2 logic
            await ctx.send(f'Team 1 has been set as {_team1}! What is the name of team 2?')
            _team2 = await self.bot.wait_for('message', check=check, timeout=60.0)
            _team2 = _team2.content
            if _team2 == 'cancel':
                return await ctx.send('Canceled setup. Rerun command to try again')

            msg = await ctx.send(f'What emote represents team {_team2}?')
            while True:
                try:
                    _team2_emote = await self.bot.wait_for('message', check=check, timeout=60.0)
                    _team2_emote = _team2_emote.content
                    if _team2_emote == 'cancel':
                        return await ctx.send('Canceled setup. Rerun command to try again')
                    await msg.add_reaction(_team2_emote)
                    await msg.remove_reaction(_team2_emote, self.bot.user)
                    break

                except (discord.NotFound, TypeError, ValueError, discord.HTTPException):
                    await ctx.send(
                        f'{config.redTick} That is not a valid emoji, please send a valid unicode or custom emoji'
                    )

            await ctx.send(f'What role represents team {_team2}? (Please send the ID)')
            while True:
                try:
                    _role = await self.bot.wait_for('message', check=check, timeout=60.0)
                    _role = _role.content
                    if _role == 'cancel':
                        return await ctx.send('Canceled setup. Rerun command to try again')
                    _team2_role = ctx.guild.get_role(int(_role))
                    if not _team2_role:
                        raise ValueError
                    break

                except ValueError:
                    await ctx.send(f'{config.redTick} That is not a valid role, please send a valid role ID')

            # Team 3 Logic
            await ctx.send(
                f'Team 2 has been set as {_team2}! What is the name of team 3? (Type "skip" for only 2 teams)'
            )
            _team3 = await self.bot.wait_for('message', check=check, timeout=60.0)
            _team3 = _team3.content
            if _team3 == 'cancel':
                return await ctx.send('Canceled setup. Rerun command to try again')

            if _team3 == 'skip':
                await ctx.send(f'Team 3 has been skipped.')
                _team3 = None
            else:
                msg = await ctx.send(f'What emote represents team {_team3}?')
                while True:
                    try:
                        _team3_emote = await self.bot.wait_for('message', check=check, timeout=60.0)
                        _team3_emote = _team3_emote.content
                        if _team3_emote == 'cancel':
                            return await ctx.send('Canceled setup. Rerun command to try again')
                        await msg.add_reaction(_team3_emote)
                        await msg.remove_reaction(_team3_emote, self.bot.user)
                        break

                    except (discord.NotFound, TypeError, ValueError, discord.HTTPException):
                        await ctx.send(
                            f'{config.redTick} That is not a valid emoji, please send a valid unicode or custom emoji'
                        )

                await ctx.send(f'What role represents team {_team3}? (Please send the ID)')
                while True:
                    try:
                        _role = await self.bot.wait_for('message', check=check, timeout=60.0)
                        _role = _role.content
                        if _role == 'cancel':
                            return await ctx.send('Canceled setup. Rerun command to try again')
                        _team3_role = ctx.guild.get_role(int(_role))
                        if not _team3_role:
                            raise ValueError
                        break

                    except ValueError:
                        await ctx.send(f'{config.redTick} That is not a valid role, please send a valid role ID')

                await ctx.send(f'Team 3 has been set as {_team3}!')

            # Channel logic
            await ctx.send(f'What channel should team choosing be in? (Please send the ID)')
            while True:
                try:
                    _channel = await self.bot.wait_for('message', check=check, timeout=60.0)
                    _channel = _channel.content
                    if _channel == 'cancel':
                        return await ctx.send('Canceled setup. Rerun command to try again')
                    _channel = ctx.guild.get_channel(int(_channel))
                    if not _channel:
                        raise ValueError
                    break

                except ValueError:
                    await ctx.send(f'{config.redTick} That is not a valid channel, please send a valid channel ID')

        except discord.Forbidden:
            return await ctx.send(
                f'{config.redTick} I am missing react permissions, please resolve this and rerun the command'
            )

        except asyncio.TimeoutError:
            return await ctx.send(f'{config.redTick} Timed out waiting for response. Rerun command to try again')

        self.team1 = {'name': _team1, 'emote': _team1_emote, 'role': _team1_role.id}
        self.team2 = {'name': _team2, 'emote': _team2_emote, 'role': _team2_role.id}
        if _team3 is None:
            self.team3 = None
        else:
            self.team3 = {'name': _team3, 'emote': _team3_emote, 'role': _team3_role.id}

        self.channel = _channel.id
        self.ACTIVE = True
        await ctx.send(
            f'{config.greenTick} The event is now activated! To end the event, run `{ctx.prefix}splatfest end`'
        )

    @commands.has_any_role(config.moderator, config.eh)
    @_splatfest.command(name='end')
    async def _splatfest_end(self, ctx):
        self.ACTIVE = False
        self.channel = None
        self.team1 = None
        self.team2 = None
        self.team3 = None
        await ctx.send(f'{config.greenTick} Splatfest ended!')

    @commands.Cog.listener()
    async def on_message(self, message):
        if self.ACTIVE and message.channel.id in [config.commandsChannel, self.channel]:
            team1Emote = re.compile(f'({self.team1["emote"]})+', re.I)
            team1Role = message.guild.get_role(self.team1['role'])
            team2Emote = re.compile(f'({self.team2["emote"]})+', re.I)
            team2Role = message.guild.get_role(self.team2['role'])
            if self.team3:
                team3Emote = re.compile(f'({self.team3["emote"]})+', re.I)
                team3Role = message.guild.get_role(self.team3['role'])
            else:
                team3Emote = None
                team3Role = None

            if re.search(team1Emote, message.content) and re.search(team2Emote, message.content):
                # If the user puts both roles in the same message, toss it
                return

            try:
                if re.search(team1Emote, message.content):
                    if team2Role in message.author.roles:
                        await message.author.remove_roles(team2Role)

                    if self.team3 and team3Role in message.author.roles:
                        await message.author.remove_roles(team3Role)

                    if team1Role not in message.author.roles:
                        msg = await message.channel.send(
                            f'<@{message.author.id}> You are now registered as a member of Team {self.team1["name"]}',
                            delete_after=10,
                        )
                        await msg.delete(delay=5.0)
                        await message.author.add_roles(team1Role)

                elif re.search(team2Emote, message.content):
                    if team1Role in message.author.roles:
                        await message.author.remove_roles(team1Role)

                    if self.team3 and team3Role in message.author.roles:
                        await message.author.remove_roles(team3Role)

                    if team2Role not in message.author.roles:
                        msg = await message.channel.send(
                            f'<@{message.author.id}> You are now registered as a member of Team {self.team2["name"]}',
                            delete_after=10,
                        )
                        await msg.delete(delay=5.0)
                        await message.author.add_roles(team2Role)

                elif self.team3 and re.search(team3Emote, message.content):
                    if team1Role in message.author.roles:
                        await message.author.remove_roles(team1Role)

                    if team2Role in message.author.roles:
                        await message.author.remove_roles(team2Role)

                    if team3Role not in message.author.roles:
                        msg = await message.channel.send(
                            f'<@{message.author.id}> You are now registered as a member of Team {self.team3["name"]}',
                            delete_after=10,
                        )
                        await msg.delete(delay=5.0)
                        await message.author.add_roles(team3Role)

            except (discord.Forbidden, discord.HTTPException):
                raise

    # Only allow one role during the event if roles are assigned by other means
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if self.ACTIVE:
            if before.roles != after.roles:
                team1Role = before.guild.get_role(self.team1['role'])
                team2Role = before.guild.get_role(self.team2['role'])
                team3Role = before.guild.get_role(self.team3['role']) if self.team3 else None

                event_roles = [team1Role.id, team2Role.id]
                if self.team3:
                    event_roles.append(team3Role.id)

                beforeCounter = collections.Counter(before.roles)
                afterCounter = collections.Counter(after.roles)

                rolesAdded = list(afterCounter - beforeCounter)

                for roleAdded in rolesAdded:
                    if roleAdded.id in event_roles:
                        for role in before.roles:
                            if role.id in event_roles:
                                await after.remove_roles(role)


async def setup(bot):
    await bot.add_cog(Splatfest(bot))
    logging.info('[Extension] Splatfest module loaded')


async def teardown(bot):
    await bot.remove_cog('Splatfest')
    logging.info('[Extension] Splatfest module unloaded')
