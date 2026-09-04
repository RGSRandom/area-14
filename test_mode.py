import discord
from discord.ext import commands
import logging
from pathlib import Path
from dotenv import load_dotenv
import os
import json
import sys

# Basic logging: simple terminal timestamps (HH:MM) and concise messages
stream_handler = logging.StreamHandler()
stream_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M')
stream_handler.setFormatter(stream_formatter)
logging.basicConfig(level=logging.INFO, handlers=[stream_handler])
logger = logging.getLogger(__name__)

# Load local environment variables from .env before reading BOT_TOKEN
load_dotenv(dotenv_path=Path(__file__).with_name('.env'))

# Load config
try:
    with open(Path(__file__).with_name('config.json'), 'r', encoding='utf-8') as f:
        config = json.load(f)

    with open(Path(__file__).with_name('dangerous_perms.json'), 'r', encoding='utf-8') as f:
        dangerous_perms = json.load(f)
except FileNotFoundError as exc:
    logger.error(f"Missing required file: {exc.filename}")
    sys.exit(1)
except json.JSONDecodeError as exc:
    logger.error(f"Invalid JSON in {exc.filename}: {exc}")
    sys.exit(1)


def build_role_mapping():
    role_mapping = {}
    for mapping in config["role_mappings"]:
        role_mapping[mapping["source_role_id"]] = mapping["target_role_id"]
    return role_mapping


def get_managed_target_role_ids():
    return {mapping["target_role_id"] for mapping in config["role_mappings"]}


def get_source_guild_ids():
    raw_source_ids = config.get("SOURCE_GUILD_IDS")
    if raw_source_ids is None:
        raw_source_ids = config.get("SOURCE_GUILD_ID")

    if raw_source_ids is None:
        return []

    if isinstance(raw_source_ids, list):
        source_ids = raw_source_ids
    else:
        source_ids = [raw_source_ids]

    return [int(source_id) for source_id in source_ids if source_id not in (None, "")]


def get_test_user_id():
    # Require an explicit TEST_USER_ID in config or env for safe test mode.
    tid = config.get("TEST_USER_ID")
    if tid not in (None, ""):
        return int(tid)
    env_tid = os.getenv("TEST_USER_ID")
    if env_tid not in (None, ""):
        return int(env_tid)
    raise ValueError("TEST_USER_ID must be set to a valid Discord user ID before running test mode")


token = os.getenv("BOT_TOKEN")
if not token:
    logger.error("BOT_TOKEN not found in environment. Set BOT_TOKEN or use .env before running this test script.")
    sys.exit(1)

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


@bot.event
async def on_ready():
    logger.info(f"Test bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("TEST MODE: Periodic sync is disabled in this script.")
    test_user = get_test_user_id()
    logger.info(f"TEST MODE: Watching role changes only for user ID {test_user}")


@bot.event
async def on_member_update(before, after):
    # Only respond to role changes
    before_role_ids = {role.id for role in before.roles}
    after_role_ids = {role.id for role in after.roles}
    if before_role_ids == after_role_ids:
        return

    # Only watch the configured test user.
    test_user = get_test_user_id()
    if after.id != test_user:
        return

    source_guild_ids = get_source_guild_ids()
    TARGET_GUILD_ID = config["TARGET_GUILD_ID"]

    # Only trigger if this happened in one of the configured source servers
    if after.guild.id not in source_guild_ids:
        return

    # Resolve target guild and member (if available)
    target_guild = bot.get_guild(TARGET_GUILD_ID)
    if not target_guild:
        logger.error(f"Target guild {TARGET_GUILD_ID} not found (test mode)")
        return

    target_member = target_guild.get_member(after.id)
    if not target_member:
        logger.warning(f"Member {after.id} not found in target guild (test mode)")
        return

    role_mapping = build_role_mapping()

    # Report roles that would be added
    new_role_ids = after_role_ids - before_role_ids
    for added_role_id in new_role_ids:
        target_role_id = role_mapping.get(added_role_id)
        if not target_role_id:
            continue
        target_role = target_guild.get_role(target_role_id)
        if not target_role:
            logger.error(f"Target role {target_role_id} not found (test mode)")
            continue

        # Check for dangerous permissions
        dangerous_perms_list = dangerous_perms.get("dangerous_permissions", [])
        role_permissions = target_role.permissions
        dangerous_found = [perm for perm in dangerous_perms_list if getattr(role_permissions, perm, False)]
        if dangerous_found:
            logger.warning(f"TEST MODE BLOCK: Role '{target_role.name}' has dangerous permissions: {dangerous_found}")
            logger.warning(f"   Would NOT give role to {after.name} (test mode)")
            continue

        # In test mode we only log the action rather than performing it
        logger.info(f"TEST MODE: Would ADD role '{target_role.name}' to {after.name} in target server")

    # Report roles that would be removed
    removed_role_ids = before_role_ids - after_role_ids
    for removed_role_id in removed_role_ids:
        target_role_id = role_mapping.get(removed_role_id)
        if not target_role_id:
            continue
        target_role = target_guild.get_role(target_role_id)
        if not target_role:
            logger.error(f"Target role {target_role_id} not found (test mode)")
            continue

        logger.info(f"TEST MODE: Would REMOVE role '{target_role.name}' from {after.name} in target server")


if __name__ == "__main__":
    logger.info("=========================")
    logger.info("Starting Discord Role Sync - TEST MODE")
    logger.info("Periodic sync disabled; script will only WATCH and LOG actions for testing.")
    try:
        get_test_user_id()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("Test bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in test bot: {e}")
        sys.exit(1)
