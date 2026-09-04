from discord.ext import commands

import main


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="debug")
    async def debug(self, ctx):
        await main.debug(ctx)

    @commands.command(name="sync")
    async def sync(self, ctx):
        await main.perform_manual_sync(ctx)

    @commands.command(name="dm")
    async def dm(self, ctx, user: commands.UserConverter, *, message: str = None):
        await main.dm_user(ctx, user, message)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
