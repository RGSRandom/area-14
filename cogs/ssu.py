from discord.ext import commands

import main


class SsuCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="beg")
    @commands.cooldown(1, 300, commands.BucketType.default)
    @commands.cooldown(1, 600, commands.BucketType.user)
    async def beg(self, ctx):
        if not main.is_allowed_ssu_staff(ctx.author.id):
            return
        await main.beg(ctx)

    @commands.command(name="poll")
    async def poll(self, ctx, *, time: str = None):
        await main.create_poll(ctx, time=time)

    @commands.command(name="ssu")
    async def ssu(self, ctx):
        await main.ssu_command(ctx)


async def setup(bot):
    await bot.add_cog(SsuCog(bot))
