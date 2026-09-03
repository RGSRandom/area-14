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
        lines.append(f"`{prefix}credits` - Show project credits")
        lines.append(f"`{prefix}uinfo <userid>` - Show a member's information and roles")
        lines.append(f"`{prefix}avatar [user]` - Show a user's avatar")

        if ctx.guild and (
            ctx.author.guild_permissions.manage_messages
            or main.is_controlled_user(ctx.author.id)
        ):
            lines.append(f"`{prefix}pin` - Pin the message being replied to")

        if main.is_controlled_user(ctx.author.id):
            lines.extend([
                f"`{prefix}sync` - Synchronize roles",
                f"`{prefix}debug` - Show bot status",
                f"`{prefix}dm <userid> <message>` - Send a direct message",
                f"`{prefix}execute @user` - Timeout a member",
                f"`{prefix}heal @user` - Remove a member timeout",
                f"`{prefix}tellhim @user` - Send a message to a member",
                f"`{prefix}punish` - Open the punishment form",
                f"`{prefix}show <punishment_id>` - Show punishment details",
                f"`{prefix}list` - Show the last 3 punishments",
                f"`{prefix}factioninfo <id or name>` - Show faction details",
                f"`{prefix}appeal <punishment_id>` - Appeal a punishment",
                f"`{prefix}revoke <punishment_id>` - Revoke a punishment",
            ])

        if main.is_allowed_ticket_staff(ctx.author):
            lines.extend([
                f"`{prefix}add @user` - Add someone to a ticket",
                f"`{prefix}remove @user` - Remove someone from a ticket",
                f"`{prefix}&^V1mticket` - Open the support ticket panel",
                f"`{prefix}&^V2hticket` - Open the staff ticket panel",
            ])

        if main.is_allowed_ssu_staff(ctx.author):
            lines.extend([
                f"`{prefix}ssu` - Open the SSU mode picker",
                f"`{prefix}poll <time>` - Create an SSU poll",
                f"`{prefix}beg` - Send an SSU reminder",
            ])

        embed = info_embed("Available Commands", "\n".join(lines), requested_by=ctx.author)
        try:
            await ctx.author.send(embed=embed)
            await ctx.reply("Check DMs :)")
        except commands.Forbidden:
            await ctx.send(
                "I could not send you a DM. Please enable direct messages from server members.",
                delete_after=5,
            )

    @commands.command(name="credits")
    async def credits(self, ctx):
        description = (
            "**Lead Developer:** RGSRandom\n"
            "**Assistant Developer:** Cipher\n\n"
         
        )
        embed = info_embed("Credits", description, requested_by=ctx.author)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
