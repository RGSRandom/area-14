"""Shared Discord embed styling for command responses."""

import discord

EMBED_COLOR = discord.Color.from_rgb(90, 164, 193)
SUCCESS_COLOR = discord.Color.from_rgb(76, 175, 80)
ERROR_COLOR = discord.Color.from_rgb(198, 70, 70)
INFO_COLOR = discord.Color.from_rgb(90, 164, 193)

BRAND_NAME = "Area - 14 AIC"
BRAND_ICON_URL = (
    "https://media.discordapp.net/attachments/1506041053344698379/"
    "1526271006266757231/area-14-1.jpg?format=webp"
)
DEFAULT_FOOTER = "Area - 14"


def create_embed(
    title=None,
    description=None,
    *,
    color=EMBED_COLOR,
    footer_text=None,
    footer_icon_url=BRAND_ICON_URL,
    author_name=BRAND_NAME,
    author_icon_url=BRAND_ICON_URL,
    requested_by=None,
    timestamp=False,
):
    """Create an embed using the bot's shared visual format."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow() if timestamp else None,
    )
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon_url)
    if requested_by is not None:
        requester_name = getattr(requested_by, "name", str(requested_by))
        footer_text = f"Requested by {requester_name}"
        footer_icon_url = getattr(requested_by, "display_avatar", None)
        footer_icon_url = getattr(footer_icon_url, "url", None) or BRAND_ICON_URL
    elif footer_text is None:
        footer_text = DEFAULT_FOOTER
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_icon_url)
    return embed


def info_embed(title, description=None, **kwargs):
    return create_embed(title, description, color=INFO_COLOR, **kwargs)


def success_embed(title, description=None, **kwargs):
    return create_embed(title, description, color=SUCCESS_COLOR, **kwargs)


def error_embed(title, description=None, **kwargs):
    return create_embed(title, description, color=ERROR_COLOR, **kwargs)