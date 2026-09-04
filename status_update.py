#!/usr/bin/env python3

"""Interactively send a branded status update to a Discord channel."""

import asyncio
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

from embed_template import create_embed


REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")


def prompt_for_update():
    channel_id_text = input("Channel ID: ").strip()
    try:
        channel_id = int(channel_id_text)
    except ValueError as exc:
        raise ValueError("Channel ID must be a number.") from exc

    title = input("Update title: ").strip()
    if not title:
        raise ValueError("Update title cannot be empty.")

    message = input("Update message: ").strip()
    if not message:
        raise ValueError("Update message cannot be empty.")

    if len(title) > 256:
        raise ValueError("Update title must be 256 characters or fewer.")
    if len(message) > 4096:
        raise ValueError("Update message must be 4096 characters or fewer.")

    return channel_id, title, message


async def send_update(channel_id, title, message):
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing from .env.")

    client = discord.Client(intents=discord.Intents.none())

    try:
        await client.login(token)
        channel = await client.fetch_channel(channel_id)
        embed = create_embed(title, message, timestamp=True)
        sent_message = await channel.send(embed=embed)
        print(f"Status update sent to #{channel_id} (message {sent_message.id}).")
    finally:
        await client.close()


def main():
    try:
        channel_id, title, message = prompt_for_update()
        asyncio.run(send_update(channel_id, title, message))
    except (ValueError, RuntimeError, discord.DiscordException) as exc:
        print(f"Unable to send status update: {exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nStatus update cancelled.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()