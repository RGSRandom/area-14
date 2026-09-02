from discord.ext import commands

import main


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="add")
    async def add(self, ctx, user: commands.MemberConverter = None):
        await main.add_to_ticket(ctx, user)

    @commands.command(name="remove")
    async def remove(self, ctx, user: commands.MemberConverter = None):
        await main.remove_from_ticket(ctx, user)

    @commands.command(name="&^V1mticket")
    async def ticket_main(self, ctx):
        await main.ticket_commandmain(ctx)

    @commands.command(name="&^V2hticket")
    async def ticket_hub(self, ctx):
        await main.ticket_commandhub(ctx)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
