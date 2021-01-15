import asyncio
import logging
import re

import discord
from discord.ext import commands

import config

class Splatfest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ACTIVE = False
        self.team1 = None
        self.team2 = None

    @commands.has_any_role(config.moderator, config.eh)
    @commands.group(name='splatfest', invoke_without_command=True)
    async def _splatfest(self, ctx):
        def check(m):
            return m.author == ctx.author

        try:
            # Team 1 logic
            await ctx.send('Welcome to Splatfest setup! You may respond "cancel" at any time to cancel setup. Now, lets get the teams down for this event -- what is the name of team 1?')
            _team1 = await self.bot.wait_for('message', check=check, timeout=30.0)
            _team1 = _team1.content
            if _team1 == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')

            msg = await ctx.send(f'What emote represents team {_team1}?')
            while True:
                try:
                    _team1_emote = await self.bot.wait_for('message', check=check, timeout=30.0)
                    _team1_emote = _team1_emote.content
                    if _team1_emote == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')
                    await msg.add_reaction(_team1_emote)
                    await msg.remove_reaction(_team1_emote, self.bot.user)
                    break

                except (discord.NotFound, discord.InvalidArgument):
                    await ctx.send(f'{config.redTick} That is not a valid emoji, please send a valid unicode or custom emoji')

            await ctx.send(f'What role represents team {_team1}? (Please send the ID)')
            while True:
                try:
                    _role = await self.bot.wait_for('message', check=check, timeout=30.0)
                    _role = _role.content
                    if _role == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')
                    _team1_role = ctx.guild.get_role(int(_role))
                    if not _team1_role: raise ValueError
                    break

                except ValueError:
                    await ctx.send(f'{config.redTick} That is not a valid role, please send a valid role ID')

            # Team 2 logic
            await ctx.send(f'Team 1 has been set as {_team1}! What is the name of team 2?')
            _team2 = await self.bot.wait_for('message', check=check, timeout=30.0)
            _team2 = _team2.content
            if _team2 == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')

            msg = await ctx.send(f'What emote represents team {_team2}?')
            while True:
                try:
                    _team2_emote = await self.bot.wait_for('message', check=check, timeout=30.0)
                    _team2_emote = _team2_emote.content
                    if _team2_emote == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')
                    await msg.add_reaction(_team2_emote)
                    await msg.remove_reaction(_team2_emote, self.bot.user)
                    break

                except (discord.NotFound, discord.InvalidArgument):
                    await ctx.send(f'{config.redTick} That is not a valid emoji, please send a valid unicode or custom emoji')

            await ctx.send(f'What role represents team {_team2}? (Please send the ID)')
            while True:
                try:
                    _role = await self.bot.wait_for('message', check=check, timeout=30.0)
                    _role = _role.content
                    if _role == 'cancel': return await ctx.send('Canceled setup. Rerun command to try again')
                    _team2_role = ctx.guild.get_role(int(_role))
                    if not _team2_role: raise ValueError
                    break

                except ValueError:
                    await ctx.send(f'{config.redTick} That is not a valid role, please send a valid role ID')

        except discord.Forbidden:
            return await ctx.send(f'{config.redTick} I am missing react permissions, please resolve this and rerun the command')

        except asyncio.TimeoutError:
            return await ctx.send(f'{config.redTick} Timed out waiting for response. Rerun command to try again')

        self.team1 = {
            'name': _team1,
            'emote': _team1_emote,
            'role': _team1_role
        },
        self.team2 = {
            'name': _team2,
            'emote': _team2_emote,
            'role': _team2_role
        }
        self.ACTIVE = True
        await ctx.send(f'{config.greenTick} Team 2 has been set as {_team2}. The event is now activated! To end the event, run `{ctx.prefix}splatfest end`')

    @commands.has_any_role(config.moderator, config.eh)
    @_splatfest.command(name='end')
    async def _splatfest_end(self, ctx):
        self.ACTIVE = False
        self.team1 = None
        self.team2 = None
        await ctx.send(f'{config.greenTick} Splatfest ended!')

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id == config.commandsChannel and self.ACTIVE:
            team1Emote = re.compile(rf'({self.team1["emote"]})+', re.I)
            team1Role = message.guild.get_role(self.team1['role'])
            team2Emote = re.compile(rf'({self.team2["emote"]})+', re.I)
            team2Role = message.guild.get_role(self.team2['role'])

            if re.search(team1Emote, message.content) and re.search(team2Emote, message.content):
                # If the user puts both roles in the same message, toss it
                return

            try:
                if re.search(team1Emote, message.content):
                    if team2Role in message.author.roles:
                        await message.author.remove_roles(team2Role)

                    if team1Role not in message.author.roles:
                        msg = await message.channel.send(f'<@{message.author.id}> You are now registered as a member of Team {self.team1["name"]}', delete_after=10)
                        await msg.delete(delay=5.0)
                        await message.author.add_roles(team1Role)

                elif re.search(team2Emote, message.content):
                    if team1Role in message.author.roles:
                        await message.author.remove_roles(team1Role)

                    if team2Role not in message.author.roles:
                        msg = await message.channel.send(f'<@{message.author.id}> You are now registered as a member of Team {self.team2["name"]}', delete_after=10)
                        await msg.delete(delay=5.0)
                        await message.author.add_roles(team2Role)

            except (discord.Forbidden, discord.HTTPException):
                pass

def setup(bot):
    bot.add_cog(Splatfest(bot))
    logging.info('[Extension] Splatfest module loaded')

def teardown(bot):
    bot.remove_cog('Splatfest')
    logging.info('[Extension] Splatfest module unloaded')
