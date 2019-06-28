from discord.ext import commands

@commands.command()
async def mute(ctx):
    await ctx.send('Works?')

def setup(bot):
    bot.add_command(mute)
