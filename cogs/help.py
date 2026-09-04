from discord.ext import commands

import main
from embed_template import info_embed


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx):
        prefix = self.bot.command_prefix
        lines = [f"`{prefix}help` - Show this message"]
        lines.append(f"`{prefix}avatar [user]` - Show a user's avatar")

        if ctx.guild and ctx.author.guild_permissions.manage_messages:
            lines.append(f"`{prefix}pin` - Pin the message being replied to")

        if main.is_controlled_user(ctx.author.id):
            lines.extend([
                f"`{prefix}sync` - Synchronize roles",
                f"`{prefix}debug` - Show bot status",
                f"`{prefix}dm <userid> <message>` - Send a direct message",
            ])

        if main.is_allowed_ticket_staff(ctx.author.id):
            lines.extend([
                f"`{prefix}add @user` - Add someone to a ticket",
                f"`{prefix}remove @user` - Remove someone from a ticket",
            ])

        if main.is_allowed_ssu_staff(ctx.author.id):
            lines.extend([
                f"`{prefix}ssu` - Open the SSU mode picker",
                f"`{prefix}poll <time>` - Create an SSU poll",
                f"`{prefix}beg` - Send an SSU reminder",
            ])

        embed = info_embed("Available Commands", "\n".join(lines), requested_by=ctx.author)
        try:
            await ctx.author.send(embed=embed)
        except commands.Forbidden:
            await ctx.send(
                "I could not send you a DM. Please enable direct messages from server members.",
                delete_after=5,
            )


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
