from discord.ext import commands

import main


class PunishmentsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="punish")
    async def punish(self, ctx, query: str, punishment_type: str, appealable: str, *, full: str = None):
        await main.punish(ctx, query, punishment_type, appealable, full=full)

    @commands.command(name="show")
    async def show(self, ctx, punishment_id: str = None):
        await main.show_punishment(ctx, punishment_id)

    @commands.command(name="appeal")
    async def appeal(self, ctx, punishment_id: str):
        await main.appeal(ctx, punishment_id)

    @commands.command(name="revoke")
    async def revoke(self, ctx, punishment_id: str):
        await main.revoke(ctx, punishment_id)


async def setup(bot):
    await bot.add_cog(PunishmentsCog(bot))
