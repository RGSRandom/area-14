from discord.ext import commands

import main


class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="execute")
    async def execute(self, ctx, user: commands.MemberConverter):
        await main.execute_member(ctx, user)

    @commands.command(name="heal")
    async def heal(self, ctx, user: commands.MemberConverter):
        await main.heal_member(ctx, user)

    @commands.command(name="tellhim")
    async def tellhim(self, ctx, user: commands.MemberConverter):
        await main.tell_user(ctx, user)

    @commands.command(name="avatar")
    async def avatar(self, ctx, user: commands.UserConverter = None):
        await main.show_avatar(ctx, user)

    @commands.command(name="pin")
    async def pin(self, ctx):
        await main.pin_replied_message(ctx)


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
