import io

import discord
from discord.ext import commands, tasks
import logging
from pathlib import Path
from dotenv import load_dotenv
import os
import json
import sys
import asyncio
import re
import math
import chat_exporter
from datetime import datetime, timedelta
from embed_template import create_embed, error_embed, info_embed, success_embed
try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    gspread = None
    Credentials = None
from discord.ui import View, Button

script_path = Path(__file__).resolve()
repo_root = script_path.parent if script_path.parent.name.lower() != "py" else script_path.parent.parent
load_dotenv(dotenv_path=repo_root / '.env')

# Setup logging
log_stream = sys.stdout
if hasattr(log_stream, 'reconfigure'):
    try:
        log_stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

# File handler: keep full timestamp for logfile
log_file_path = (repo_root / 'discord.log').resolve()
file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Stream handler: simpler console logging with HH:MM timestamp only
stream_handler = logging.StreamHandler(log_stream)
stream_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M')
stream_handler.setFormatter(stream_formatter)
stream_handler.setLevel(logging.WARNING)

# Explicitly configure the root logger so file + console writes always happen.
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)

logger = logging.getLogger(__name__)

# Config file paths
config_file_path = (repo_root / 'json' / 'config.json').resolve()
dangerous_perms_path = (repo_root / 'json' / 'dangerous_perms.json').resolve()

channel_log_id = 1533412186850988093
channel_log_ticket = 1533516881305145435
channel_log_ticket_hub = 1379696175053148210

active_ticket_creations = set()

PUNISHMENTS_FILE = Path("json/punishments.json")

def load_punishments():
    if not PUNISHMENTS_FILE.exists():
        return []
    try:
        with open(PUNISHMENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_punishments(data):
    with open(PUNISHMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# Load config files helper functions
def load_config():
    try:
        with config_file_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"[DEBUG] Loaded config.json from {config_file_path} with {len(data.get('role_mappings', [])) if isinstance(data.get('role_mappings'), list) else 'N/A'} role mappings")
        return data
    except Exception as exc:
        logger.error(f"Failed to load config.json from {config_file_path}: {exc}")
        raise


def load_dangerous_perms():
    try:
        with dangerous_perms_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"[DEBUG] Loaded dangerous_perms.json from {dangerous_perms_path}")
        return data
    except Exception as exc:
        logger.error(f"Failed to load dangerous_perms.json from {dangerous_perms_path}: {exc}")
        raise

config = load_config()
dangerous_perms = load_dangerous_perms()

# Debug: show loaded config and dangerous_perms summary to help diagnose loading issues
try:
    config_path = (repo_root / 'json' / 'config.json').resolve()
    dp_path = (repo_root / 'json' / 'dangerous_perms.json').resolve()
    logger.info(f"[DEBUG] Loaded config from: {config_path}")
    logger.info(f"[DEBUG] Loaded dangerous_perms from: {dp_path}")
    # Show top-level keys and some key values
    logger.info(f"[DEBUG] config keys: {sorted(list(config.keys()))}")
    logger.info(f"[DEBUG] TARGET_GUILD_ID: {config.get('TARGET_GUILD_ID')}")
    logger.info(f"[DEBUG] SOURCE_GUILD_ID(S): {config.get('SOURCE_GUILD_ID') or config.get('SOURCE_GUILD_IDS')}")
    rm = config.get('role_mappings')
    logger.info(f"[DEBUG] role_mappings raw type: {type(rm)}")
    logger.info(f"[DEBUG] role_mappings count: {len(rm) if isinstance(rm, list) else 'N/A'}")
    if isinstance(rm, list):
        unique_source_ids = {mapping.get('source_role_id') for mapping in rm if isinstance(mapping, dict)}
        logger.info(f"[DEBUG] role_mappings count: {len(rm)}")
        logger.info(f"[DEBUG] unique source_role_id count: {len(unique_source_ids)}")
    else:
        logger.info(f"[DEBUG] role_mappings is not a list: {type(rm)}")

    logger.info(f"[DEBUG] dangerous_perms keys: {sorted(list(dangerous_perms.keys()))}")
    logger.info(f"[DEBUG] dangerous_permissions list (sample): {dangerous_perms.get('dangerous_permissions')[:10] if dangerous_perms.get('dangerous_permissions') else []}")
except Exception as e:
    logger.error(f"[DEBUG] Error while printing config debug info: {e}")

required_config_keys = {"TARGET_GUILD_ID", "role_mappings"}
missing_keys = required_config_keys - config.keys()
if missing_keys:
    logger.error(f"Missing config keys: {sorted(missing_keys)}")
    sys.exit(1)

if "SOURCE_GUILD_ID" not in config and "SOURCE_GUILD_IDS" not in config:
    logger.error("Missing SOURCE_GUILD_ID or SOURCE_GUILD_IDS in config.json")
    sys.exit(1)

if not isinstance(config["role_mappings"], list) or not config["role_mappings"]:
    logger.error("config.json role_mappings must be a non-empty list")
    sys.exit(1)

def build_role_mapping(config_data=None):
    """Build a mapping of source_role_id -> list of target_role_ids.

    A single source role can map to more than one target role (e.g. one
    config.json entry for the department role, another for an "Area High
    Rank" role sharing the same source_role_id). Using a list here (instead
    of overwriting a single value) ensures every configured target role is
    kept rather than only the last one seen for a given source_role_id.
    """
    if config_data is None:
        config_data = load_config()
    role_mapping = {}
    for mapping in config_data["role_mappings"]:
        if "source_role_id" not in mapping or "target_role_id" not in mapping:
            raise ValueError("Each role mapping must include source_role_id and target_role_id")
        role_mapping.setdefault(mapping["source_role_id"], []).append(mapping["target_role_id"])
    logger.info(f"[DEBUG] build_role_mapping produced {len(role_mapping)} source entries "
                f"({sum(len(v) for v in role_mapping.values())} total target roles)")
    return role_mapping


def get_managed_target_role_ids(config_data=None):
    if config_data is None:
        config_data = load_config()
    return {mapping["target_role_id"] for mapping in config_data["role_mappings"]}


def get_source_guild_ids(config_data=None):
    if config_data is None:
        config_data = load_config()
    raw_source_ids = config_data.get("SOURCE_GUILD_IDS")
    if raw_source_ids is None:
        raw_source_ids = config_data.get("SOURCE_GUILD_ID")

    if raw_source_ids is None:
        return []

    if isinstance(raw_source_ids, list):
        source_ids = raw_source_ids
    else:
        source_ids = [raw_source_ids]

    return [int(source_id) for source_id in source_ids if source_id not in (None, "")]


def get_test_user_id(config_data=None):
    if config_data is None:
        config_data = load_config()
    test_user_id = config_data.get("TEST_USER_ID")
    if test_user_id in (None, ""):
        return None
    return int(test_user_id)


def get_config_ids(config_data, key):
    raw_ids = config_data.get(key, [])
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    return {
        int(identifier)
        for identifier in raw_ids
        if identifier not in (None, "")
    }


def get_config_user_ids(config_data, key):
    return get_config_ids(config_data, key)


def is_member_in_configured_roles(member, config_data, key):
    configured_role_ids = get_config_ids(config_data, key)
    return any(role.id in configured_role_ids for role in member.roles)


def is_test_mode_enabled(config_data=None):
    if config_data is None:
        config_data = load_config()
    if "TEST_MODE" in os.environ:
        return os.environ["TEST_MODE"].strip().lower() in {"1", "true", "yes", "on"}
    return bool(config_data.get("TEST_MODE", False))


def should_sync_user(user_id, config_data=None):
    if config_data is None:
        config_data = load_config()
    if not is_test_mode_enabled(config_data):
        return True

    test_user_id = get_test_user_id(config_data)
    if test_user_id is None:
        raise ValueError("TEST_USER_ID must be set before using test mode")
    return user_id == test_user_id


ALLOWED_CONTROL_USER_IDS = get_config_user_ids(config, "CONTROL_USER_IDS")
_sync_enabled = True


def is_controlled_user(user_id):
    return user_id in ALLOWED_CONTROL_USER_IDS


def is_allowed_ticket_staff(member, config_data=None):
    if config_data is None:
        config_data = load_config()
    return is_member_in_configured_roles(
        member, config_data, "ALLOWED_TICKET_STAFF_ROLE_IDS"
    )


def is_allowed_ssu_staff(member, config_data=None):
    if config_data is None:
        config_data = load_config()
    return is_member_in_configured_roles(
        member, config_data, "ALLOWED_SSU_STAFF_ROLE_IDS"
    )


def is_sync_enabled():
    return _sync_enabled


def set_sync_enabled(enabled):
    global _sync_enabled
    _sync_enabled = enabled
token = os.getenv("BOT_TOKEN")

intent = discord.Intents.default()
intent.message_content = True
intent.members = True
intent.guilds=True

bot = commands.Bot(command_prefix='a!', intents=intent, help_command=None)   

embed_color = discord.Color.from_rgb(90, 164, 193)
embed_author_name = {"name": "Area - 14 AIC"}
embed_author_icon = {"icon_url": "https://media.discordapp.net/attachments/1506041053344698379/1526271006266757231/area-14-1.jpg?ex=6a6ecde4&is=6a6d7c64&hm=dc583901adf693945b78c9b5df0092bf126e6ef344725a8755937414e336888d&=&format=webp"}
embed_footer_text = {"text": "Area - 14 Ticket System"}
embed_footer_icon = {"icon_url": "https://media.discordapp.net/attachments/1506041053344698379/1526271006266757231/area-14-1.jpg?ex=6a6ecde4&is=6a6d7c64&hm=dc583901adf693945b78c9b5df0092bf126e6ef344725a8755937414e336888d&=&format=webp"}
embed_footer_text_ssu = {"text": "Area - 14 Server Start Up Notifier"}
embed_footer_icon_ssu = {"icon_url": "https://media.discordapp.net/attachments/1506041053344698379/1526271006266757231/area-14-1.jpg?ex=6a6ecde4&is=6a6d7c64&hm=dc583901adf693945b78c9b5df0092bf126e6ef344725a8755937414e336888d&=&format=webp"}


views_loaded = False

@bot.event
async def on_ready():
    print("=== Registered Commands ===")
    for cmd in sorted(bot.commands, key=lambda c: c.name):
        print(cmd.name)
    global views_loaded
    print("=" * 40)
    print("READY")
    print("PID:", os.getpid())
    print("Bot:", bot.user)
    print("=" * 40)
    logger.info(f'✅ Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guild(s)')
    # Debug: show whether the members intent is enabled for this bot
    logger.info(f"[DEBUG] bot.intents.members={bot.intents.members}")
    config_data = load_config()
    source_guild_ids = get_source_guild_ids(config_data)
    target_guild_id = config_data["TARGET_GUILD_ID"]
    logger.info(f'Watching for role syncs from Guild(s) {source_guild_ids} → {target_guild_id}')

    # Debug: resolve and print source/target guild resolution status
    for sid in source_guild_ids:
        try:
            g = bot.get_guild(int(sid))
            logger.info(f"[DEBUG] source guild {sid} resolved: {bool(g)} (name={g.name if g else 'N/A'})")
        except Exception:
            logger.info(f"[DEBUG] source guild {sid} resolution raised an exception")

    try:
        tg = bot.get_guild(target_guild_id)
        logger.info(f"[DEBUG] target guild resolved: {bool(tg)} (name={tg.name if tg else 'N/A'})")
    except Exception:
        logger.info("[DEBUG] target guild resolution raised an exception")

    logger.info("=" * 50)
    logger.info("⏭️ Skipping startup role sync (disabled)")
    logger.info("=" * 50)

    # Start the periodic sync afterwards
    if not sync_roles.is_running():
        sync_roles.start()
        logger.info("🔄 Started periodic role sync (every 30 minutes)")
    if not views_loaded:
        views_loaded = True
        bot.add_view(SupportTicketView())
        bot.add_view(StaffTicketView())
        bot.add_view(ApprovalView())   # ← no argument

@tasks.loop(minutes=60)
async def sync_roles():
    """Sync roles every 30 minutes to ensure consistency"""
    if not is_sync_enabled():
        logger.info("Sync paused; skipping periodic role sync")
        return

    logger.info("🔄 Running periodic role sync...")
    
    config_data = load_config()
    source_guild_ids = get_source_guild_ids(config_data)
    TARGET_GUILD_ID = config_data["TARGET_GUILD_ID"]
    
    # Get target guild and all configured source guilds
    target_guild = bot.get_guild(TARGET_GUILD_ID)
    source_guilds = [bot.get_guild(source_guild_id) for source_guild_id in source_guild_ids]
    
    if not target_guild:
        logger.error(f"Target guild {TARGET_GUILD_ID} not found")
        return

    if not source_guilds or any(source_guild is None for source_guild in source_guilds):
        logger.error(f"One or more source guilds were not found: {source_guild_ids}")
        return
    
    # Build mapping of source role IDs to target role IDs
    role_mapping = build_role_mapping(config_data)
    managed_target_role_ids = get_managed_target_role_ids(config_data)

    if is_test_mode_enabled(config_data):
        test_user_id = get_test_user_id(config_data)
        if test_user_id is None:
            logger.warning("TEST_MODE is enabled but TEST_USER_ID is missing; skipping periodic sync")
            return
    
    # Get all members from target guild
    target_members = target_guild.members
    synced_count = 0
    
    
    for target_member in target_members:
        excluded_role_ids = get_excluded_role_ids(config_data)
        member_role_ids = {role.id for role in target_member.roles}

        if member_role_ids & excluded_role_ids:
            logger.info(
                f"Skipping {target_member.name} because they are fucking quarantined"
            )
            continue
        try:
            if is_test_mode_enabled(config_data) and target_member.id != test_user_id:
                continue

            # Try to find this member in each configured source guild
            should_have_target_roles = set()
            for source_guild in source_guilds:
                source_member = source_guild.get_member(target_member.id)
                if not source_member:
                    continue

                for source_role in source_member.roles:
                    if source_role.id in role_mapping:
                        should_have_target_roles.update(role_mapping[source_role.id])

            if not should_have_target_roles:
                continue
            
            # Get roles they actually have in target from our mappings
            current_mapped_target_roles = {target_role.id for target_role in target_member.roles if target_role.id in managed_target_role_ids}
            
            # Remove roles they shouldn't have
            roles_to_remove = current_mapped_target_roles - should_have_target_roles
            for role_id in roles_to_remove:
                role = target_guild.get_role(role_id)
                if role:
                    await target_member.remove_roles(role)
                    channel = bot.get_channel(channel_log_id)

                    embed = discord.Embed(
                        title="🗑️  |  ROLE REMOVED",
                        description=f"Removed role <@&{role.id}> from user <@{target_member.id}>.",
                        color=embed_color
                    )
                    embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                    embed.set_footer(text=target_member.name, icon_url=target_member.display_avatar.url)
                    await channel.send(embed=embed)
                    logger.info(f"🗑️ Sync removed '{role.name}' from {target_member.name}")
                    synced_count += 1
            
            # Add roles they should have but don't
            roles_to_add = should_have_target_roles - current_mapped_target_roles
            for role_id in roles_to_add:
                role = target_guild.get_role(role_id)
                if not role:
                    continue

                dangerous_perms_list = dangerous_perms.get("dangerous_permissions", [])
                if any(getattr(role.permissions, perm, False) for perm in dangerous_perms_list):
                    logger.warning(f"Blocked adding '{role.name}' due to dangerous perms")
                    continue

                await target_member.add_roles(role)
                channel = bot.get_channel(channel_log_id)
                if channel is None:
                    logger.error(f"Log channel {channel_log_id} not found")
                    synced_count += 1
                    continue

                embed = discord.Embed(
                    title="✅  |  ROLE ADDED",
                    description=f"Added role <@&{role.id}> to user <@{target_member.id}>.",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=target_member.name, icon_url=target_member.display_avatar.url)
                await channel.send(embed=embed)
                logger.info(f"✅ Sync added <@&{role.id}> to {target_member.name}")
                synced_count += 1
        
        except Exception as e:
            logger.error(f"Error syncing member {target_member.name}: {e}")
    
    logger.info(f"✅ Periodic sync complete! {synced_count} role changes made")

@bot.event
async def on_member_update(before, after):
    # Debug: show before/after role IDs and guild context
    before_role_ids = {role.id for role in before.roles}
    after_role_ids = {role.id for role in after.roles}
    logger.info(f"[DEBUG] on_member_update triggered for user {after.id} in guild {after.guild.id}")
    logger.info(f"[DEBUG] before roles: {sorted(before_role_ids)}")
    logger.info(f"[DEBUG] after roles:  {sorted(after_role_ids)}")

    if before_role_ids == after_role_ids:
        logger.info("[DEBUG] No role changes detected returning")
        return  # No role changes

    # Load server IDs from config
    config_data = load_config()
    source_guild_ids = get_source_guild_ids(config_data)
    TARGET_GUILD_ID = config_data["TARGET_GUILD_ID"]

    logger.info(f"[DEBUG] Config source_guild_ids={source_guild_ids}, TARGET_GUILD_ID={TARGET_GUILD_ID}")

    if not is_sync_enabled():
        logger.info("[DEBUG] Sync is paused; skipping live role update handling")
        return

    try:
        if not should_sync_user(after.id, config_data):
            logger.info(f"[DEBUG] Ignoring member update for {after.id}; not the configured test user")
            return
    except ValueError as exc:
        logger.error(str(exc))
        return

    # Only trigger if this happened in one of the configured source servers
    if after.guild.id not in source_guild_ids:
        logger.info(f"[DEBUG] Event guild {after.guild.id} not in configured source guilds - ignoring")
        return

    # Get target guild
    target_guild = bot.get_guild(TARGET_GUILD_ID)
    if not target_guild:
        logger.error(f"[DEBUG] Target guild {TARGET_GUILD_ID} not found")
        return

    # Get the member in the target server
    target_member = target_guild.get_member(after.id)
    if not target_member:
        logger.warning(f"[DEBUG] Member {after.id} not found in target guild")
        return
    
    excluded_role_ids = get_excluded_role_ids(config_data)
    target_member_role_ids = {role.id for role in target_member.roles}

    if target_member_role_ids & excluded_role_ids:
        logger.info(
            f"Skipping live sync for {target_member.name}; "
            "they have an excluded role"
        )
        return

    role_mapping = build_role_mapping(config_data)
    current_dangerous_perms = load_dangerous_perms()
    logger.info(f"[DEBUG] Role mapping has {len(role_mapping)} entries")

    # Check for roles that were added
    new_role_ids = after_role_ids - before_role_ids
    logger.info(f"[DEBUG] New role IDs: {sorted(new_role_ids)}")
    for added_role_id in new_role_ids:
        logger.info(f"[DEBUG] Processing added source role id: {added_role_id}")
        target_role_ids = role_mapping.get(added_role_id)
        if not target_role_ids:
            logger.info(f"[DEBUG] No mapping for source role {added_role_id}")
            continue  # No mapping for this role

        for target_role_id in target_role_ids:
            try:
                target_role = target_guild.get_role(target_role_id)
                if not target_role:
                    logger.error(f"[DEBUG] Target role {target_role_id} not found")
                    continue

                dangerous_perms_list = current_dangerous_perms.get("dangerous_permissions", [])
                role_permissions = target_role.permissions

                dangerous_found = []
                for perm in dangerous_perms_list:
                    has = getattr(role_permissions, perm, False)
                    logger.info(f"[DEBUG] Role '{target_role.name}' perm {perm}: {has}")
                    if has:
                        dangerous_found.append(perm)

                if dangerous_found:
                    channel = bot.get_channel(channel_log_id)

                    embed = discord.Embed(
                        title="🚫  |  AUTO-ROLE BLOCKED",
                        description=f"Role <@&{target_role.id}> has dangerous permissions: {dangerous_found}. <@{after.id}>'s roles were not assigned.",
                        color=embed_color
                    )
                    embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                    embed.set_footer(text=after.name, icon_url=after.display_avatar.url)
                    await channel.send (embed=embed)
                    logger.warning(f"🚫 BLOCKED: Role '{target_role.name}' has dangerous permissions: {dangerous_found}")
                    logger.warning(f"   User {after.name} was NOT given this role. Edit dangerous_perms.json if needed.")
                    continue

                logger.info(f"[DEBUG] Attempting to add role '{target_role.name}' ({target_role.id}) to user {after.id}")
                await target_member.add_roles(target_role)
                channel = bot.get_channel(channel_log_id)

                embed = discord.Embed(
                    title="✅  |  ROLE ADDED",
                    description=f"Added role <@&{target_role.id}> to user <@{after.id}>.",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=after.name, icon_url=after.display_avatar.url)

                await channel.send(embed=embed)
                logger.info(f"✅ Added role '{target_role.name}' to {after.name} in target server")

            except Exception as e:
                channel = bot.get_channel(channel_log_id)

                embed = discord.Embed(
                    title="🔒    |  ERROR",
                    description=f"Error syncing {target_member.mention}: {e}",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=after.name, icon_url=after.display_avatar.url)   
                await channel.send(embed=embed)            
                logger.error(f"Error syncing member {target_member.name}: {e}", exc_info=True)

    # Check for roles that were removed
    removed_role_ids = before_role_ids - after_role_ids
    if removed_role_ids:
        # Recalculate what target roles the user SHOULD still have
        should_still_have = set()
        for source_role in after.roles:          # current roles after the removal
            if source_role.id in role_mapping:
                should_still_have.update(role_mapping[source_role.id])

        for removed_role_id in removed_role_ids:
            target_role_ids = role_mapping.get(removed_role_id)
            if not target_role_ids:
                continue

            for target_role_id in target_role_ids:
                # Only remove the target role if the user no longer has ANY source role that gives it
                if target_role_id in should_still_have:
                    continue

                try:
                    target_role = target_guild.get_role(target_role_id)
                    if not target_role:
                        continue

                    await target_member.remove_roles(target_role)

                    channel = bot.get_channel(channel_log_id)
                    if channel:
                        embed = discord.Embed(
                            title="🗑️  |  ROLE REMOVED",
                            description=f"Removed role <@&{target_role.id}> from user <@{after.id}>.",
                            color=embed_color
                        )
                        embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                        embed.set_footer(text=after.name, icon_url=after.display_avatar.url)
                        await channel.send(embed=embed)

                    logger.info(f"🗑️ Removed role '{target_role.name}' from {after.name}")

                except Exception as e:
                    logger.error(f"Error removing role: {e}", exc_info=True)

# --- Manual `a!sync` command and concurrency guard ---
# Track active syncs per target guild to prevent cross-source conflicts
active_sync_targets = set()
async def debug(ctx):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        return
    config_data = load_config()

    embed = info_embed("Bot Status", requested_by=ctx.author)
    bot_user = bot.user
    bot_status = (
        f"{bot_user}\nID: `{bot_user.id}`"
        if bot_user is not None
        else "Unavailable (failed to load bot identity)"
    )
    latency_ms = bot.latency * 1000
    latency_status = (
        f"{round(latency_ms)} ms"
        if math.isfinite(latency_ms)
        else "Unavailable (failed to measure latency)"
    )

    embed.add_field(
        name="Bot",
        value=bot_status,
        inline=False
    )

    embed.add_field(
        name="Latency",
        value=latency_status,
        inline=True
    )

    embed.add_field(
        name="Guilds",
        value=str(len(bot.guilds)),
        inline=True
    )

    embed.add_field(
        name="Users Cached",
        value=str(len(bot.users)),
        inline=True
    )

    embed.add_field(
        name="Sync Enabled",
        value="Enabled" if is_sync_enabled() else "Disabled",
        inline=True
    )

    embed.add_field(
        name="Periodic Sync",
        value="Running" if sync_roles.is_running() else "Stopped",
        inline=True
    )

    embed.add_field(
        name="Test Mode",
        value="Enabled" if is_test_mode_enabled(config_data) else "Disabled",
        inline=True
    )

    embed.add_field(
        name="Target Guild",
        value=f"`{config_data['TARGET_GUILD_ID']}`",
        inline=False
    )

    embed.add_field(
        name="Source Guild(s)",
        value="\n".join(f"`{gid}`" for gid in get_source_guild_ids(config_data)),
        inline=False
    )

    embed.add_field(
        name="Role Mappings",
        value=str(len(config_data["role_mappings"])),
        inline=True
    )

    embed.add_field(
        name="Dangerous Permissions",
        value=str(len(dangerous_perms.get("dangerous_permissions", []))),
        inline=True
    )

    embed.add_field(
        name="Python",
        value=sys.version.split()[0],
        inline=True
    )

    embed.set_footer(text=f"Area - 14 | PID: {os.getpid()}")

    await ctx.send(embed=embed)

async def perform_manual_sync(ctx):
    if not is_sync_enabled():
        if ctx:
            await ctx.channel.send(
                embed=discord.Embed(
                    title="Sync Paused",
                    description="Sync is currently paused. Use a!start first.",
                    color=embed_color()
                )
            )
        return

    config_data = load_config()
    source_guild_ids = get_source_guild_ids(config_data)
    TARGET_GUILD_ID = config_data["TARGET_GUILD_ID"]
    current_dangerous_perms = load_dangerous_perms()

    target_guild = bot.get_guild(TARGET_GUILD_ID)
    source_guilds = [bot.get_guild(sid) for sid in source_guild_ids]

    if not target_guild or any(s is None for s in source_guilds):
        embed = discord.Embed(title="Error: Guilds not available",
                              description="One or more configured servers are not available to the bot. Please check the bot's permissions.",
                              color=embed_color)
        if ctx:
            await ctx.channel.send(embed=embed)
        else:
            logger.error("Startup sync: one or more configured servers are not available to the bot.")
        return

    if TARGET_GUILD_ID in active_sync_targets:
        embed = discord.Embed(title="Sync Already Running",
                              description="A sync is already running for the target server; cannot start another.",
                              color=embed_color)
        if ctx:
            await ctx.channel.send(embed=embed)
        else:
            logger.warning("Startup sync: a sync is already running for the target server; skipping.")
        return

    # Mark sync active for this target
    active_sync_targets.add(TARGET_GUILD_ID)
    embed = discord.Embed(title="Manual Sync Started",
                            description="Fetching members and syncing roles.",
                            color=discord.Color.blue())
    if ctx:
        status_msg = await ctx.channel.send(embed=embed)
    else:
        status_msg = None

    # Fetch full member lists to avoid relying on partial cache
    try:
        target_members = [m async for m in target_guild.fetch_members(limit=None)]
    except Exception:
        # Fall back to cached members if fetching fails
        target_members = list(target_guild.members)

    if is_test_mode_enabled(config_data):
        test_user_id = get_test_user_id(config_data)
        if test_user_id is None:
            msg = "Test mode enabled. TEST_USER_ID wasn't found in config."
            if ctx:
                await ctx.channel.send(msg)
            else:
                logger.error(msg)
            return

    source_members_map = {}
    for src in source_guilds:
        try:
            members = [m async for m in src.fetch_members(limit=None)]
            source_members_map[src.id] = {m.id: m for m in members}
        except Exception:
            source_members_map[src.id] = {m.id: m for m in src.members}

    total_members = len(target_members)
    processed = 0
    changes = 0
    stop_progress = False

    async def progress_updater():
        try:
            while not stop_progress:
                embed = discord.Embed(title="Manual Sync in Progress",
                                    description=f"Processed {processed}/{total_members} members - {changes} role changes so far",
                                    color=discord.Color.blue())
                if status_msg:
                    await status_msg.edit(embed=embed)
                else:
                    logger.info(f"Startup Sync: {processed}/{total_members} members - {changes} changes")
                await asyncio.sleep(0.5)
        except Exception:
            logger.exception("Progress updater crashed")

    progress_task = asyncio.create_task(progress_updater())

    try:
        role_mapping = build_role_mapping(config_data)
        managed_target_role_ids = get_managed_target_role_ids(config_data)

        for target_member in target_members:
            excluded_role_ids = get_excluded_role_ids(config_data)
            member_role_ids = {role.id for role in target_member.roles}

            if member_role_ids & excluded_role_ids:
                logger.info(f"Skipping {target_member.name} because they have an excluded role")
                continue
            try:
                if not should_sync_user(target_member.id, config_data):
                    processed += 1
                    continue

                should_have_target_roles = set()
                for src in source_guilds:
                    src_map = source_members_map.get(src.id, {})
                    source_member = src_map.get(target_member.id)
                    if not source_member:
                        continue
                    for source_role in source_member.roles:
                        if source_role.id in role_mapping:
                            should_have_target_roles.update(role_mapping[source_role.id])

                if not should_have_target_roles:
                    processed += 1
                    continue

                current_mapped_target_roles = {r.id for r in target_member.roles if r.id in managed_target_role_ids}

                # Remove roles they shouldn't have
                roles_to_remove = current_mapped_target_roles - should_have_target_roles
                for role_id in roles_to_remove:
                    role = target_guild.get_role(role_id)
                    if role:
                        await target_member.remove_roles(role)
                        channel = bot.get_channel(channel_log_id)

                        embed = discord.Embed(
                            title="🗑️  |  ROLE REMOVED",
                            description=f"Removed role <@&{role.id}> from user <@{target_member.id}>.",
                            color=embed_color
                        )
                        embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                        embed.set_footer(text=target_member.name, icon_url=target_member.display_avatar.url)
                        await channel.send(embed=embed)
                        logger.info(f"Removed '{role.name}' from {target_member.name}")
                        changes += 1

                # Add roles they should have but don't
                roles_to_add = should_have_target_roles - current_mapped_target_roles
                for role_id in roles_to_add:
                    role = target_guild.get_role(role_id)
                    if role:
                        dangerous_perms_list = current_dangerous_perms.get("dangerous_permissions", [])
                        role_permissions = role.permissions
                        if any(getattr(role_permissions, perm, False) for perm in dangerous_perms_list):
                            logger.warning(f"Blocked adding '{role.name}' due to dangerous perms")
                            continue
                        await target_member.add_roles(role)
                        channel = bot.get_channel(channel_log_id)

                        embed = discord.Embed(
                            title=" ✅  |  ROLE ADDED",
                            description=f"Added role <@&{role.id}> to user <@{target_member.id}>.",
                            color=embed_color
                        )
                        embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                        embed.set_footer(text=target_member.name, icon_url=target_member.display_avatar.url)

                        await channel.send(embed=embed)
                        logger.info(f"Added '{role.name}' to {target_member.name}")
                        changes += 1

                processed += 1

            except Exception as e:
                channel = bot.get_channel(channel_log_id)

                embed = discord.Embed(
                    title="🔒  |  ERROR",
                    description=f"Error syncing {target_member.mention}: {e}",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=target_member.name, icon_url=target_member.display_avatar.url) 
                logger.error(f"Error syncing member {target_member.name}: {e}", exc_info=True)
                await channel.send(embed=embed)

        stop_progress = True
        await progress_task
        embed =discord.Embed(title="Manual Sync Complete",
                            description=f"Processed {processed}/{total_members} members - {changes} role changes made",
                            color=discord.Color.green())
        if status_msg:
            await status_msg.edit(embed=embed)
        else:
            logger.info(f"✅ Startup sync complete: Processed {processed}/{total_members} members - {changes} role changes made")

    finally:
        stop_progress = True

        if not progress_task.done():
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        active_sync_targets.discard(TARGET_GUILD_ID)

from discord.ext import commands

SSU_BEG_CHANNEL_ID = 1363910034936955053  # ← put the target channel ID here

import random  # make sure this is at the top of your file

async def beg(ctx):
    channel = bot.get_channel(SSU_BEG_CHANNEL_ID)
    
    if channel is None:
        await ctx.send("ERROR")
        return

    embed = discord.Embed(
        title="🚨🚨🚨 SSU Beg 🚨🚨🚨",
        description=(
            "🚨🚨🚨 **SSU EMERGENCY - DAY 9000** 🚨🚨🚨\n\n"
            "hElP mE. 😭🙏\n\n"
            "I hAvE bEeN wAiTiNg FoR aN SSU fOr So LoNg ThAt I hAvE cOmPlEtEd ThE eNtIrE **FIVE STAGES OF GRIEF** tWiCe.\n\n"
            "😃 **DENIAL:**\n\n"
            "\"tHeY'rE pRoBaBlY jUsT pRePaRiNg.\"\n\n"
            "😡 **ANGER:**\n\n"
            "\"WHO HAS THE SSU BUTTON?!?!?!\"\n\n"
            "🤝 **BARGAINING:**\n\n"
            "\"pLeAsE... i'Ll bE cLaSs-D... i'Ll ClEaN tHe FaCiLiTy... I'LL EVEN FOLLOW THE RULES.\"\n\n"
            "😭 **DEPRESSION:**\n\n"
            "*opens #announcements*\n\n"
            "*nothing.*\n\n"
            "*refreshes.*\n\n"
            "*nothing.*\n\n"
            "*refreshes again.*\n\n"
            "**NOTHING.**\n\n"
            "🧘 **ACCEPTANCE:**\n\n"
            "\"I have accepted that there will never be an SSU.\"\n\n"
            "...\n\n"
            "**NO I HAVEN'T.** 💀💀💀\n\n"
            "I pReSsEd F5 sO mAnY tImEs My KeYbOaRd HaS dEvElOpEd SeLf-AwArEnEsS. ⌨️🧠\n\n"
            "I rEsTaRtEd DiScOrD.\n\n"
            "I rEsTaRtEd RoBlOx.\n\n"
            "I rEsTaRtEd My Pc.\n\n"
            "I rEsTaRtEd My RoUtEr. 🔌\n\n"
            "I uNpLuGgEd My MoNiToR. 🖥️\n\n"
            "I aM nOw CoNfIdEnT iN mY tEcHnIcAl AbIlItIeS.\n\n"
            "I cAn FiX tHe InTeRnEt.\n\n"
            "I cAn FiX tHe RoUtEr.\n\n"
            "I cAn FiX tHe FaCiLiTy.\n\n"
            "**BUT I CANNOT FIX THE LACK OF AN SSU.** 😭😭😭\n\n"
            "I wEnT oUtSiDe. 🌳\n\n"
            "I tOuChEd GrAsS. 🌱\n\n"
            "I sAw A bIrD. 🐦\n\n"
            "I aSkEd It WhEn ThE nExT SSU wAs.\n\n"
            "iT fLeW aWaY.\n\n"
            "**EVEN THE BIRD KNOWS.** 😭\n\n"
            "I tRiEd TaLkInG tO rEaL pEoPlE.\n\n"
            "\"wHaT aRe YoU dOiNg?\"\n\n"
            "\"wAiTiNg FoR aN SSU.\"\n\n"
            "tHeY hAvE nOt ReTuRnEd. 💀\n\n"
            "eVeRy DiScOrD pInG iS nOw A pSyChOlOgIcAl EvEnT. 🔔\n\n"
            "**SSU?!?!?!**\n\n"
            "nO.\n\n"
            "sOmEoNe PoStEd \"gm.\"\n\n"
            "I hAvE bEeN rUiNeD. 😭\n\n"
            "SSUHs...\n\n"
            "I kNoW yOu'Re ThErE.\n\n"
            "I kNoW yOu CaN sEe ThIs.\n\n"
            "I kNoW tHe **START SERVER** bUtToN eXiStS.\n\n"
            "pLeAsE...\n\n"
            "🙏😭🙏😭🙏😭🙏😭🙏\n\n"
            "**P R E S S   I T .**\n\n"
            "🚨🚨🚨 **START THE SSU** 🚨🚨🚨\n\n"
            "**I BEG YOUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUU** 😭😭😭😭😭"
        ),
        color=discord.Color.green()
    )

    ROLE_1_IN_100 = 1300127296476151909     
    ROLE_1_IN_10000 = 1417497758130245752   
    ROLE_1_IN_1000000 = 1398431646520311809  

    content = ctx.author.mention

    roll = random.randint(1, 1000000)

    if roll == 1:  # 1 in 1,000,000
        content += f"Lucky roll! 1 in 1000000 (0.0001%!) <@&{ROLE_1_IN_1000000}> Pinging poverty toilet!"
    elif roll <= 100:  # 1 in 10,000
        content += f"Lucky roll! 1 in 10000 (0.0099%!) <@&{ROLE_1_IN_10000}> Pinging General Staff!"
    elif roll <= 10000:  # 1 in 100
        content += f"Lucky roll! 1 in 100 (0.99%!) <@&{ROLE_1_IN_100}> Pinging SSUT!"

    print(roll)

    await ctx.message.add_reaction("✅")
    await channel.send(content=content, embed=embed)



SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

google_sheets_config = config.get("GOOGLE_SHEETS", {})
SERVICE_ACCOUNT_FILE = repo_root / google_sheets_config.get(
    "CREDENTIALS_FILE", "CREDENTIALS.json"
)
SPREADSHEET_KEY = google_sheets_config.get("SPREADSHEET_KEY")
INFRACTIONS_WORKSHEET = google_sheets_config.get(
    "INFRACTIONS_WORKSHEET", "Infractions Database"
)
FACTION_WORKSHEET = google_sheets_config.get(
    "FACTION_WORKSHEET", "Faction Database"
)

gc = None
if not google_sheets_config.get("ENABLED", True):
    logger.info("Google Sheets integration disabled by config.json")
elif not SPREADSHEET_KEY:
    logger.warning(
        "Google Sheets integration disabled; GOOGLE_SHEETS.SPREADSHEET_KEY is missing"
    )
elif gspread is not None and Credentials is not None:
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
    except (OSError, ValueError) as exc:
        logger.warning(
            "Google Sheets integration disabled; punishment commands are unavailable: %s",
            exc,
        )
else:
    logger.warning(
        "Google Sheets integration disabled; install gspread and google-auth to enable punishment commands"
    )

SPREADSHEET_KEY = "18_otfKSSCYRFf87R3Rmo3oQT3QvcYV_2_l0MepQKZlY"

import random
import string

def generate_punishment_id():
    date_part = datetime.now().strftime("%y%m%d")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PUN-{date_part}-{random_part}"

class ApprovalView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def disable_all(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except:
            pass

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="approve_punishment")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.disable_all(interaction)

        punishments = load_punishments()
        found = None
        for entry in punishments:
            if entry.get("message_id") == interaction.message.id:
                found = entry
                break

        if not found:
            await interaction.followup.send("Punishment not found in database.", ephemeral=True)
            return

        if found["status"] != "awaiting_approval":
            await interaction.followup.send(f"This punishment is already `{found['status']}`.", ephemeral=True)
            return

        found["status"] = "active"
        found["approved_by"] = str(interaction.user)
        found["approved_by_id"] = interaction.user.id
        save_punishments(punishments)

        # Update Google Sheet
        try:
            sheet = gc.open_by_key(SPREADSHEET_KEY).worksheet(INFRACTIONS_WORKSHEET)
            sheet_main = gc.open_by_key(SPREADSHEET_KEY).worksheet(FACTION_WORKSHEET)

            cell = sheet.find(found["faction_id"], in_column=3)
            cell_main = sheet_main.find(found["faction_id"], in_column=3)

            row = cell.row
            row_main = cell_main.row          # ← was wrong before (you used cell.row)

            # Read the Leader (column H = 8)
            leader_id = sheet_main.cell(row_main, 8).value


            if "Warning" in found["punishment"]:
                sheet.update_cell(row, 7, found["punishment"])
            elif "Strike" in found["punishment"]:
                sheet.update_cell(row, 6, found["punishment"])
        except Exception as e:
            await interaction.followup.send(f"JSON updated but failed to update sheet: `{e}`", ephemeral=True)

        # Post final embed to punish channel
        channel = await resolve_channel(PUNISH_CHANNEL_ID)
        if channel:
            ping_role = None
            if interaction.guild:
                for role in interaction.guild.roles:
                    if role.name.startswith(found["faction_id"]) or role.name.startswith(f"[{found['faction_id']}]"):
                        ping_role = role
                        break
            content = ping_role.mention if ping_role else None

            embed = discord.Embed(title="Faction Infraction", color=embed_color)
            embed.add_field(name="Faction Name", value=found["faction_name"], inline=False)
            embed.add_field(name="Reason", value=found["reason"], inline=False)
            embed.add_field(name="Punishment", value=found["punishment"], inline=True)
            embed.add_field(name="Appealable", value="Yes" if found["appealable"] else "No", inline=True)
            embed.add_field(name="Status", value="Active", inline=True)

            if found["appealable"]:
                embed.add_field(name="Appeal Method", value="HC+ open a appeal ticket.", inline=False)

            embed.add_field(name="Proof", value=found.get("proof", "No proof"), inline=False)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            if found.get("anonymous", False):
                embed.set_footer(text=f"Punishment ID: {found['punishment_id']}")
            else:
                embed.set_footer(
                    text=f"Requested by {found['punished_by']} | Approved by {interaction.user} | ID: {found['punishment_id']}",
                    icon_url=interaction.user.display_avatar.url
                )

            final_msg = await channel.send(content=f"{content} <@{leader_id}>", embed=embed)

            # IMPORTANT: update the message_id so appeal/revoke reply to the public message
            found["message_id"] = final_msg.id
            save_punishments(punishments)

        # Update the original approval message
        try:
            original_embed = interaction.message.embeds[0]
            original_embed.set_field_at(4, name="Status", value="Approved", inline=True)
            original_embed.set_footer(
                text=f"Requested by {found['punished_by']} | Approved by {interaction.user} | ID: {found['punishment_id']}"
            )
            await interaction.message.edit(embed=original_embed, view=self)
        except:
            pass

        await interaction.followup.send(
            f"Punishment `{found['punishment_id']}` has been **approved** and posted.", ephemeral=True
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_punishment")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.disable_all(interaction)

        punishments = load_punishments()
        found = None
        for entry in punishments:
            if entry.get("message_id") == interaction.message.id:
                found = entry
                break

        if not found:
            await interaction.followup.send("Punishment not found in database.", ephemeral=True)
            return

        if found["status"] != "awaiting_approval":
            await interaction.followup.send(f"This punishment is already `{found['status']}`.", ephemeral=True)
            return

        found["status"] = "denied"
        found["denied_by"] = str(interaction.user)
        found["denied_by_id"] = interaction.user.id
        save_punishments(punishments)

        try:
            original_embed = interaction.message.embeds[0]
            original_embed.set_field_at(4, name="Status", value="Denied", inline=True)
            original_embed.set_footer(
                text=f"Requested by {found['punished_by']} | Denied by {interaction.user} | ID: {found['punishment_id']}"
            )
            await interaction.message.edit(embed=original_embed, view=self)
        except:
            pass

        await interaction.followup.send(
            f"Punishment `{found['punishment_id']}` has been **denied**.", ephemeral=True
        )

PUNISH_CHANNEL_ID = config.get("PUNISH_CHANNEL_ID")

ACCEPT_CHANNEL_ID=1478069999410217010
APPROVAL_ENABLED = False

async def resolve_channel(channel_id, guild=None):
    channel = guild.get_channel(channel_id) if guild else None
    if channel is None:
        channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except Exception as exc:
        logger.exception("Unable to resolve Discord channel %s: %s", channel_id, exc)
        return None

class PunishmentModal(discord.ui.Modal, title="Faction Infraction"):
    faction = discord.ui.TextInput(
        label="Faction name or ID",
        placeholder="Enter the faction name or ID",
        max_length=100,
    )
    punishment_type = discord.ui.TextInput(
        label="Punishment type",
        placeholder="warning or strike",
        max_length=20,
    )
    appealable = discord.ui.TextInput(
        label="Appealable?",
        placeholder="yes or no",
        max_length=10,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Explain the infraction. Start with y for anonymous.",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    proof = discord.ui.TextInput(
        label="Proof link or details",
        placeholder="Provide a link or describe the proof",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        logger.info("Punishment form submitted by %s", interaction.user)
        full = f"{self.reason.value.strip()} | {self.proof.value.strip()}"
        try:
            await punish(
                self.ctx,
                self.faction.value.strip(),
                self.punishment_type.value.strip(),
                self.appealable.value.strip(),
                full=full,
            )
        except Exception:
            logger.exception("Punishment form failed for %s", interaction.user)
            await interaction.followup.send(
                "The punishment form failed unexpectedly. Please check the bot logs.",
                ephemeral=True,
            )


class PunishmentFormView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=300)
        self.ctx = ctx

    @discord.ui.button(label="Open punishment form", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the staff member who started this form can use it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(PunishmentModal(self.ctx))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


async def start_punishment_form(ctx):
    has_faction_management_role = 1346192810998501396 in {
        role.id for role in ctx.author.roles
    }
    if not has_faction_management_role and not is_controlled_user(ctx.author.id):
        await ctx.send("You do not have permission to use the punishment form.")
        return
    await ctx.send(
        "Complete the infraction form below.",
        view=PunishmentFormView(ctx),
    )


async def punish(ctx, query: str, punishment_type: str, appealable: str, *, full: str = None):
    # 1. Role checks
    fm_role = 1346192810998501396

    user_role_ids = [role.id for role in ctx.author.roles]

    if fm_role not in user_role_ids and not is_controlled_user(ctx.author.id):
        await ctx.send("You do not have permission to issue faction punishments.")
        return

    if gc is None:
        await ctx.send(
            "Google Sheets is unavailable. The punishment was not applied."
        )
        return

    # 2. Parse full text
    if full is None:
        await ctx.send("Please provide a reason.")
        return

    parts = full.strip().split(maxsplit=1)
    is_anonymous = False

    if parts and parts[0].lower() == "y":
        is_anonymous = True
        full = parts[1] if len(parts) > 1 else ""
    else:
        is_anonymous = False

    if not full:
        await ctx.send("Please provide a reason.")
        return

    if "|" in full:
        reason, proof = full.split("|", 1)
        reason = reason.strip()
        proof = proof.strip()
    else:
        reason = full.strip()
        proof = None

    attachments = ctx.message.attachments
    if attachments:
        attachment_links = "\n".join(a.url for a in attachments)
        if proof:
            proof = f"{proof}\n{attachment_links}"
        else:
            proof = attachment_links

    if not proof:
        await ctx.send("Proof is required to punish a faction. The punishment was not applied.")
        return

    punishment_type = punishment_type.lower()
    appealable = appealable.lower()

    if punishment_type not in ("warning", "w", "strike", "s"):
        await ctx.send("Invalid punishment type. Use `warning`/`w` or `strike`/`s`.")
        return

    try:
        sheet = gc.open_by_key(SPREADSHEET_KEY).worksheet(INFRACTIONS_WORKSHEET)
        sheet_main = gc.open_by_key(SPREADSHEET_KEY).worksheet(FACTION_WORKSHEET)

        # Find the faction
        cell = sheet.find(query, in_column=3)
        cell_main = sheet_main.find(query, in_column=3)

        row = cell.row
        row_main = cell_main.row
        name = sheet.cell(row, 4).value

        # Read Leader ID from column H
        leader_id = sheet_main.cell(row_main, 8).value

        # Calculate next punishment
        if punishment_type in ("warning", "w"):
            current = sheet.cell(row, 7).value
            if current in (None, "", " "):
                next_punishment = "Warning 1"
            elif current in ("Warning 1", "Warning-1"):
                next_punishment = "Warning 2"
            else:
                next_punishment = "Warning 3"
        else:
            current = sheet.cell(row, 6).value
            if current in (None, "", " "):
                next_punishment = "Strike 1"
            elif current in ("Strike 1", "Strike-1"):
                next_punishment = "Strike 2"
            else:
                next_punishment = "Strike 3"

        punishment_id = generate_punishment_id()

        # Find role to ping
        ping_role = None
        for role in ctx.guild.roles:
            if role.name.startswith(query) or role.name.startswith(f"[{query}]"):
                ping_role = role
                break

        # Build content with role + leader
        content_parts = []
        if ping_role:
            content_parts.append(ping_role.mention)
        if leader_id:
            content_parts.append(f"<@{leader_id}>")
        content = " ".join(content_parts) if content_parts else None

        # ==========================================
        # BRANCH A: Needs Approval
        # ==========================================
        if APPROVAL_ENABLED:
            await ctx.message.add_reaction("✅")

            embed = discord.Embed(title="Faction Infraction Request", color=embed_color)
            embed.add_field(name="Faction Name", value=name, inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Punishment", value=next_punishment, inline=True)
            embed.add_field(name="Appealable", value=appealable.capitalize(), inline=True)
            embed.add_field(name="Status", value="Awaiting Approval", inline=True)
            embed.add_field(name="Anonymous", value="Yes" if is_anonymous else "No", inline=True)
            embed.add_field(name="Requested by", value=ctx.author.mention, inline=False)

            if appealable in ("yes", "y"):
                embed.add_field(name="Appeal Method", value="HC+ open an appeal ticket.", inline=False)

            embed.add_field(name="Proof", value=proof, inline=False)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed.set_footer(
                text=f"Requested by {ctx.author} | Approved by N/A | ID: {punishment_id}",
                icon_url=ctx.author.display_avatar.url
            )

            view = ApprovalView()
            approve_channel = await resolve_channel(ACCEPT_CHANNEL_ID, ctx.guild)
            if approve_channel is None:
                await ctx.send(
                    "The approval channel is unavailable. Please contact an administrator."
                )
                return
            await ctx.send(
                "You do not have permission to issue punishments directly. Your request has been sent for approval."
            )
            punish_msg = await approve_channel.send(content=content, embed=embed, view=view)

            if attachments:
                files = [await a.to_file() for a in ctx.message.attachments]
                await approve_channel.send(files=files)

            punishments = load_punishments()
            log_entry = {
                "punishment_id": punishment_id,
                "message_id": punish_msg.id,
                "faction_id": query,
                "faction_name": name,
                "punishment": next_punishment,
                "reason": reason,
                "proof": proof,
                "appealable": appealable in ("yes", "y"),
                "anonymous": is_anonymous,
                "status": "awaiting_approval",
                "punished_by": str(ctx.author),
                "punished_by_id": ctx.author.id,
                "timestamp": datetime.now().isoformat()
            }
            punishments.append(log_entry)
            save_punishments(punishments)
            return

        # ==========================================
        # BRANCH B: Direct Punishment
        # ==========================================
        else:
            if punishment_type in ("warning", "w"):
                sheet.update_cell(row, 7, next_punishment)
            else:
                sheet.update_cell(row, 6, next_punishment)

            await ctx.message.add_reaction("✅")

            embed = discord.Embed(title="Faction Infraction", color=embed_color)
            embed.add_field(name="Faction Name", value=name, inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Punishment", value=next_punishment, inline=True)
            embed.add_field(name="Appealable", value=appealable.capitalize(), inline=True)
            embed.add_field(name="Status", value="Active", inline=True)

            if appealable in ("yes", "y"):
                embed.add_field(name="Appeal Method", value="HC+ open a appeal ticket.", inline=False)

            embed.add_field(name="Proof", value=proof, inline=False)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            if is_anonymous:
                embed.set_footer(text=f"Punishment ID: {punishment_id}")
            else:
                embed.set_footer(
                    text=f"Punished by {ctx.author} | Punishment ID: {punishment_id}",
                    icon_url=ctx.author.display_avatar.url
                )

            channel = await resolve_channel(PUNISH_CHANNEL_ID, ctx.guild)
            if channel is None:
                await ctx.send(
                    "The punishment channel is unavailable. Please contact an administrator."
                )
                return
            punish_msg = await channel.send(content=content, embed=embed)

            if attachments:
                files = [await a.to_file() for a in ctx.message.attachments]
                await channel.send(files=files)

            punishments = load_punishments()
            log_entry = {
                "punishment_id": punishment_id,
                "message_id": punish_msg.id,
                "faction_id": query,
                "faction_name": name,
                "punishment": next_punishment,
                "reason": reason,
                "proof": proof,
                "appealable": appealable in ("yes", "y"),
                "anonymous": is_anonymous,
                "status": "active",
                "punished_by": str(ctx.author),
                "punished_by_id": ctx.author.id,
                "timestamp": datetime.now().isoformat()
            }
            punishments.append(log_entry)
            save_punishments(punishments)

    except Exception as e:
        await ctx.send(f"Error executing punishment: `{e}`")

def get_highest_punishment(punishments, faction_id, ptype):
    levels = []
    for entry in punishments:
        if entry["faction_id"] != faction_id or entry["status"] != "active":
            continue
        pun = entry["punishment"].replace("-", " ")
        if ptype == "warning" and pun.startswith("Warning "):
            try:
                levels.append(int(pun.split(" ")[1]))
            except:
                pass
        elif ptype == "strike" and pun.startswith("Strike "):
            try:
                levels.append(int(pun.split(" ")[1]))
            except:
                pass

    if not levels:
        return ""
    return f"{'Warning' if ptype == 'warning' else 'Strike'} {max(levels)}"

async def show_punishment(ctx, punishment_id: str = None):
    # Delete the trigger message
    try:
        await ctx.message.delete()
    except:
        pass

    if punishment_id is None:
        await ctx.send("Usage: `a!show <Punishment ID>`", delete_after=5)
        return

    # ===== Role check =====
    fm_role = 1346192810998501396
    user_role_ids = [role.id for role in ctx.author.roles]

    if fm_role not in user_role_ids:
        return

    punishments = load_punishments()
    found = None

    for entry in punishments:
        if entry["punishment_id"] == punishment_id:
            found = entry
            break

    if not found:
        await ctx.send(f"Punishment ID `{punishment_id}` not found.", delete_after=5)
        return

    embed = info_embed(f"Punishment Details: {punishment_id}", requested_by=ctx.author)
    embed.add_field(name="Faction Name", value=found.get("faction_name", "N/A"), inline=True)
    embed.add_field(name="Punishment", value=found.get("punishment", "N/A"), inline=True)
    embed.add_field(name="Appealable", value="Yes" if found.get("appealable") else "No", inline=True)
    embed.add_field(name="Reason", value=found.get("reason", "N/A"), inline=False)
    embed.add_field(name="Proof", value=found.get("proof", "N/A"), inline=False)

    embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

    await ctx.send(embed=embed)

async def appeal(ctx, punishment_id: str):
    # ===== Role check =====
    fm_role = 1346192810998501396
    user_role_ids = [role.id for role in ctx.author.roles]

    if fm_role not in user_role_ids:
        return

    punishments = load_punishments()
    found = None

    for entry in punishments:
        if entry["punishment_id"] == punishment_id:
            if entry.get("appealable") is False:
                await ctx.send("This punishment is unappealable.")
                return

            entry["status"] = "appealed"
            found = entry
            break

    if not found:
        await ctx.send("Punishment ID not found.")
        return

    save_punishments(punishments)

    # ===== Recalculate and update Google Sheet =====
    try:
        sheet = gc.open_by_key(SPREADSHEET_KEY).worksheet(INFRACTIONS_WORKSHEET)
        cell = sheet.find(found["faction_id"], in_column=3)
        row = cell.row

        highest_warning = get_highest_punishment(punishments, found["faction_id"], "warning")
        highest_strike = get_highest_punishment(punishments, found["faction_id"], "strike")

        sheet.update_cell(row, 7, highest_warning)
        sheet.update_cell(row, 6, highest_strike)

        sheet.update_note(f"G{row}", f"Last appealed: {punishment_id}" if highest_warning else "")
        sheet.update_note(f"F{row}", f"Last appealed: {punishment_id}" if highest_strike else "")

    except Exception as e:
        await ctx.send(f"JSON updated but failed to update sheet: `{e}`")
        return

    await ctx.send(f"Punishment `{punishment_id}` marked as **Appealed**.")

    # ===== Reply to original punishment message =====
    channel = bot.get_channel(PUNISH_CHANNEL_ID)
    msg_id = found.get("message_id")

    if channel and msg_id:
        try:
            original_msg = await channel.fetch_message(msg_id)
            await original_msg.reply("Punishment **appealed**.")
        except discord.NotFound:
            pass


async def revoke(ctx, punishment_id: str):
    # ===== Role check =====
    fm_role = 1346192810998501396
    user_role_ids = [role.id for role in ctx.author.roles]

    if fm_role not in user_role_ids:
        await ctx.send("You do not have permission to use this command.")
        return

    punishments = load_punishments()
    found = None

    for entry in punishments:
        if entry["punishment_id"] == punishment_id:
            entry["status"] = "REVOKED"
            found = entry
            break

    if not found:
        await ctx.send("Punishment ID not found.")
        return

    save_punishments(punishments)

    # ===== Recalculate and update Google Sheet =====
    try:
        sheet = gc.open_by_key(SPREADSHEET_KEY).worksheet(INFRACTIONS_WORKSHEET)
        cell = sheet.find(found["faction_id"], in_column=3)
        row = cell.row

        highest_warning = get_highest_punishment(punishments, found["faction_id"], "warning")
        highest_strike = get_highest_punishment(punishments, found["faction_id"], "strike")

        sheet.update_cell(row, 7, highest_warning)
        sheet.update_cell(row, 6, highest_strike)

        sheet.update_note(f"G{row}", f"Last revoked: {punishment_id}" if highest_warning else "")
        sheet.update_note(f"F{row}", f"Last revoked: {punishment_id}" if highest_strike else "")

    except Exception as e:
        await ctx.send(f"JSON updated but failed to update sheet: `{e}`")
        return

    await ctx.send(f"Punishment `{punishment_id}` marked as **REVOKED**.")

    # ===== Reply to original punishment message =====
    channel = bot.get_channel(PUNISH_CHANNEL_ID)
    msg_id = found.get("message_id")

    if channel and msg_id:
        try:
            original_msg = await channel.fetch_message(msg_id)
            await original_msg.reply("**REVOKED.**")
        except discord.NotFound:
            pass


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    else:
        raise error

TICKET_CATEGORY_IDS = {
    1389925525312507924,  # general
    1389925682678730782,  # partnership
    1509601039412625439,  # report
}

TICKET_OPENER_OVERWRITES = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    send_tts_messages=True,
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    send_voice_messages=True,
    use_application_commands=True,
)


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if channel.category_id in TICKET_CATEGORY_IDS:
        return True
    name = channel.name.lower()
    return name.startswith(("general-", "partnership-", "report-"))


async def add_to_ticket(ctx, user: discord.Member = None):
    if not is_allowed_ticket_staff(ctx.author):
        await ctx.send(
            embed=error_embed(
                "Permission Denied",
                "Only approved ticket staff can use this command.",
                requested_by=ctx.author,
            )
        )
        return

    if not is_ticket_channel(ctx.channel):
        embed = error_embed(
            title="Not a Ticket Channel",
            description="This command can only be used in ticket channels.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(
            title="Wrong Usage!",
            description="Usage: `a!add @user` or `a!add USER_ID`",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    if user.bot:
        embed = error_embed(
            title="Cannot Add Bot",
            description="You cannot add a bot to a ticket.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    if user in ctx.channel.members and ctx.channel.permissions_for(user).view_channel:
        embed = info_embed(
            title="User Already Has Access",
            description=f"{user.mention} already has access to this ticket.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    try:
        await ctx.channel.set_permissions(user, overwrite=TICKET_OPENER_OVERWRITES)
    except discord.Forbidden:
        await ctx.send("No permissions.")
        return
    except Exception as e:
        await ctx.send(f"Failed to add user: {e}")
        return

    embed = success_embed(
        title="User Added",
        description=f"{user.mention} was added to this ticket by {ctx.author.mention}.",
        requested_by=ctx.author,
    )
    await ctx.send(embed=embed)

async def remove_from_ticket(ctx, user: discord.Member = None):
    if not is_allowed_ticket_staff(ctx.author):
        await ctx.send(
            embed=error_embed(
                "Permission Denied",
                "Only approved ticket staff can use this command.",
                requested_by=ctx.author,
            )
        )
        return

    if not is_ticket_channel(ctx.channel):
        embed = error_embed(
            title="Not a Ticket Channel",
            description="This command can only be used in ticket channels.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(
            title="Wrong Usage!",
            description="Usage: `a!remove @user` or `a!remove USER_ID`",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    if user.bot:
        embed = error_embed(
            title="Cannot Remove Bot",
            description="You cannot remove a bot from a ticket.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    # Check if the user actually has access
    if not ctx.channel.permissions_for(user).view_channel:
        embed = error_embed(
            title="User Does Not Have Access",
            description=f"{user.mention} does not have access to this ticket.",
            requested_by=ctx.author,
        )
        await ctx.send(embed=embed)
        return

    try:
        await ctx.channel.set_permissions(user, overwrite=None)  # removes the permission overwrite
    except discord.Forbidden:
        await ctx.send("No permissions.")
        return
    except Exception as e:
        await ctx.send(f"Failed to remove user: {e}")
        return

    embed = success_embed(
        title="User Removed",
        description=f"{user.mention} was removed from this ticket by {ctx.author.mention}.",
        requested_by=ctx.author,
    )
    await ctx.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("fuck you"):
        await message.channel.send("No, fuck you.")
    elif message.content.startswith(("no fuck you", "No, fuck you.", "No fuck you")):
        await message.channel.send("NO FUCK YOU.")

    await bot.process_commands(message)

async def execute_member(ctx, user: discord.Member = None):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        print("Wtf?")
        return

    if user is None:
        await ctx.send("You need to mention a user.")
        return

    embed = discord.Embed(
        title="EXECUTING",
        description=f"⚠︎⚠︎⚠︎⚠︎⚠︎⚠︎ EXECUTING {user.mention} ⚠︎⚠︎⚠︎⚠︎⚠︎⚠︎",
        color=discord.Color.red()
    )

    try:
        await user.timeout(timedelta(days=27), reason="EXECUTED.")
        await ctx.send(embed=embed)
        print(f"Successfully timed out {user}")
    except discord.Forbidden:
        await ctx.send("No perms :(")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Discord error: {e}")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {e}")
        print(f"Error: {e}")

async def heal_member(ctx, user: discord.Member = None):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        print("Wtf?")
        return

    if user is None:
        await ctx.send("No")
        return

    embed = discord.Embed(
        title="HEALED",
        description=f"❤️❤️❤️❤️❤️❤️ HEALED {user.mention} ❤️❤️❤️❤️❤️❤️",
        color=discord.Color.green()
    )

    try:
        await user.timeout(None, reason="Healed.")
        await ctx.send(embed=embed)
        print(f"Successfully removed timeout from {user}")
    except discord.Forbidden:
        await ctx.send("No perms :(")
    except discord.HTTPException as e:
        await ctx.send(f"❌ Discord error: {e}")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {e}")
        print(f"Error: {e}")

@bot.command(name="test")
async def test_component(ctx: commands.Context):
    """Test Discord Components V2 payload."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    payload = {
        "flags": 1 << 15,  # IS_COMPONENTS_V2
        "components": [
            {
                "type": 17,  # Container
                "components": [
                    {
                        "type": 9,  # Section
                        "components": [
                            {
                                "type": 10,  # Text Display
                                "content": (
                                    "## Area-14 Component Test\n\n"
                                    "This is a test message rendered using Discord's "
                                    "**Components V2** container format via `a!test`."
                                ),
                            }
                        ],
                        "accessory": {
                            "type": 11,  # Thumbnail
                            "media": {
                                "url": "https://cdn.discordapp.com/embed/avatars/0.png"
                            },
                        },
                    },
                    {
                        "type": 1,  # Action Row
                        "components": [
                            {
                                "type": 2,  # Button
                                "style": 1,
                                "label": "System Operational",
                                "emoji": {"name": "⚙️"},
                                "disabled": True,
                                "custom_id": "test_btn_disabled",
                            }
                        ],
                    },
                ],
            }
        ],
    }

    route = discord.http.Route(
        "POST",
        "/channels/{channel_id}/messages",
        channel_id=ctx.channel.id,
    )
    await bot.http.request(route, json=payload)

async def tell_user(ctx, user: discord.Member = None):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        print("Wtf?")
        return

    if user is None:
        await ctx.send("No")
        return

    embed = discord.Embed(
        title="Hi, fuck you",
        description=f"Fuck you {user}",
        color=discord.Color.blurple()
    )

    try:
        await ctx.send(content=f"<@{user.id}>", embed=embed)
    except discord.HTTPException as e:
        await ctx.send(f"❌ Discord error: {e}")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {e}")
        print(f"Error: {e}")


async def show_avatar(ctx, user: discord.User = None):
    if user is None:
        user = ctx.author

    embed = info_embed(
        f"{user.display_name}'s Avatar",
        f"[Open full-size avatar]({user.display_avatar.url})",
        requested_by=ctx.author,
    )
    embed.set_image(url=user.display_avatar.url)
    await ctx.send(embed=embed)


async def show_user_info(ctx, user_id: str):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server.")
        return

    try:
        member_id = int(user_id)
    except (TypeError, ValueError):
        await ctx.send("Please provide a valid numeric user ID.")
        return

    member = ctx.guild.get_member(member_id)
    if member is None:
        try:
            member = await ctx.guild.fetch_member(member_id)
        except discord.NotFound:
            await ctx.send("That user is not a member of this server.")
            return
        except discord.HTTPException:
            await ctx.send("I could not retrieve that user's information.")
            return

    role_names = [role.mention for role in reversed(member.roles) if role != ctx.guild.default_role]
    role_value = ", ".join(role_names) if role_names else "No roles"
    if len(role_value) > 1024:
        role_value = role_value[:1021] + "..."

    permissions = [
        name.replace("_", " ").title()
        for name, enabled in member.guild_permissions if enabled
    ]
    permissions_value = ", ".join(permissions) if permissions else "No special permissions"
    if len(permissions_value) > 1024:
        permissions_value = permissions_value[:1021] + "..."

    embed = info_embed("User Information", requested_by=ctx.author)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=f"{member.mention}\n`{member}`", inline=True)
    embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="Status", value=str(member.status).title(), inline=True)
    embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "F"), inline=False)
    embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "F"), inline=False)
    embed.add_field(name="Roles", value=role_value, inline=False)
    embed.add_field(name="Permissions", value=permissions_value, inline=False)
    await ctx.send(embed=embed)


async def pin_replied_message(ctx):
    if not ctx.guild or not ctx.author.guild_permissions.manage_messages:
        await ctx.send(
            embed=error_embed(
                "Permission Denied",
                "You need the **Manage Messages** permission to use this command.",
                requested_by=ctx.author,
            )
        )
        return

    reference = ctx.message.reference
    if reference is None or reference.message_id is None:
        await ctx.send(
            embed=error_embed(
                "Reply Required",
                "Use `a!pin` as a reply to the message you want to pin.",
                requested_by=ctx.author,
            )
        )
        return

    try:
        message = reference.resolved
        if not isinstance(message, discord.Message):
            message = await ctx.channel.fetch_message(reference.message_id)

        bot_member = ctx.guild.me
        if bot_member is None or not ctx.channel.permissions_for(bot_member).manage_messages:
            await ctx.send(
                embed=error_embed(
                    "Pin Failed",
                    "I need the **Manage Messages** permission in this channel.",
                    requested_by=ctx.author,
                )
            )
            return

        await message.pin(reason=f"Pinned by {ctx.author}")
    except discord.NotFound:
        await ctx.send(
            embed=error_embed(
                "Pin Failed",
                "I could not find the message you replied to.",
                requested_by=ctx.author,
            )
        )
        return
    except discord.Forbidden:
        await ctx.send(
            embed=error_embed(
                "Pin Failed",
                "Discord denied the pin request. Check my channel permissions.",
                requested_by=ctx.author,
            )
        )
        return
    except discord.HTTPException as exc:
        await ctx.send(
            embed=error_embed(
                "Pin Failed",
                f"Discord rejected the pin request: `{exc}`",
                requested_by=ctx.author,
            )
        )
        return

    await ctx.send(
        embed=success_embed(
            "Message Pinned",
            f"[Open the pinned message]({message.jump_url})",
            requested_by=ctx.author,
        )
    )


async def dm_user(ctx, user: discord.User, message: str = None):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        await ctx.send(
            embed=error_embed(
                "Permission Denied",
                "You are not authorized to use this command.",
                requested_by=ctx.author,
            )
        )
        return

    if user is None or not message:
        await ctx.send(
            embed=error_embed(
                "Invalid Usage",
                "Usage: `a!dm [userid] [message]`",
                requested_by=ctx.author,
            )
        )
        return

    try:
        await user.send(message)
    except discord.Forbidden:
        await ctx.send(
            embed=error_embed(
                "DM Failed",
                f"I could not send a DM to **{user}**. Their DMs may be disabled.",
                requested_by=ctx.author,
            )
        )
        return
    except discord.HTTPException as exc:
        await ctx.send(
            embed=error_embed(
                "DM Failed",
                f"Discord rejected the message: `{exc}`",
                requested_by=ctx.author,
            )
        )
        return

    await ctx.send(
        embed=success_embed(
            "DM Sent",
            f"Your message was sent to **{user}**.",
            requested_by=ctx.author,
        )
    )

# ======================
# SSU + POLL SYSTEM
# ======================

SSU_ANNOUNCE_CHANNEL_ID = 1300133839175417918
SSU_POLL_CHANNEL_ID = 1300133949972021348

# ---------- Reaction Poll Command ----------
async def create_poll(ctx, *, time: str = None):
    if not is_allowed_ssu_staff(ctx.author):
        return

    if time is None:
        return

    embed = discord.Embed(
        title="Server Start Up Poll | 14 Reactions Required",
        description=f"Time - {time}",
        color=embed_color
    )
    embed.add_field(name="1️⃣ Casual", value="> This mode is for a chill and relaxing roleplay or just no roleplay.", inline=False)
    embed.add_field(name="2️⃣ Semi-Serious", value="> More stricter than Casual, with a few limitations but still chill roleplay.", inline=False)
    embed.add_field(name="3️⃣ Serious", value="> This mode is for RP demons 😈", inline=False)
    embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
    embed.set_footer(text=embed_footer_text_ssu["text"], icon_url=embed_footer_icon_ssu["icon_url"])
    embed.set_image(url="https://media.discordapp.net/attachments/1506041053344698379/1526271004874117341/IMG_20250608_023240.jpg?ex=6a8fc364&is=6a8e71e4&hm=d1e3a0205e2871306910d73a856df48045f6bc7ec700540a0d41cf2ca89e6391&=&format=webp")

    try:
        try:
            await ctx.message.delete()
        except:
            pass

        channel = ctx.guild.get_channel(SSU_POLL_CHANNEL_ID)
        if channel is None:
            await ctx.send("Poll channel not found.", delete_after=5)
            return

        msg = await channel.send(content="@here <@&1300897601750568961>", embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("1️⃣")
        await msg.add_reaction("2️⃣")
        await msg.add_reaction("3️⃣")

    except Exception as e:
        await ctx.send(f"❌ Error creating poll: {e}")


# ---------- Button SSU Command ----------
class SSUPollView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

    async def check_permission(self, interaction: discord.Interaction) -> bool:
        if is_allowed_ssu_staff(interaction.user):
            return True

        await interaction.followup.send("Access Denied.", ephemeral=True)
        return False

    async def send_ssu(self, interaction: discord.Interaction, mode: str, mode_description: str):
        await interaction.response.defer(ephemeral=True)

        if not await self.check_permission(interaction):
            return

        embed = discord.Embed(
            title="Server Start Up",
            description=f"{interaction.user.mention} is hosting an SSU. The mode for this SSU is **{mode}**. Please follow all regulations found [here](https://discord.com/channels/1297640433878433792/1300133402636320869) and enjoy your time on-site!\n\nRequest mod permissions ⁠at https://discord.com/channels/1297640433878433792/1300134062819639411\n\nRequest Morphing Services at https://discord.com/channels/1297640433878433792/1300134126955003904\n\nYou may not be modded or morphed during an event\n\nDon't forget to join our group and request roles to automatically get modded when you join!\n\n<@&1300135059357175890> your event cooldown will be 20-35 up to 1 hour per event. Use https://discord.com/channels/1297640433878433792/1508112776307019916 ",
            color=embed_color
        )
        embed.add_field(name=mode, value=mode_description, inline=False)
        embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
        embed.set_footer(text=embed_footer_text_ssu["text"], icon_url=embed_footer_icon_ssu["icon_url"])
        embed.set_image(url="https://media.discordapp.net/attachments/1506041053344698379/1526271004874117341/IMG_20250608_023240.jpg?ex=6a8fc364&is=6a8e71e4&hm=d1e3a0205e2871306910d73a856df48045f6bc7ec700540a0d41cf2ca89e6391&=&format=webp")

        channel = self.guild.get_channel(SSU_ANNOUNCE_CHANNEL_ID)
        if channel is None:
            await interaction.followup.send("SSU announcement channel not found.", ephemeral=True)
            return

        await channel.send(content="@here <@&1300897601750568961>", embed=embed)
        await interaction.followup.send(f"SSU started! yayayayay", ephemeral=True)

    @discord.ui.button(label="Casual", style=discord.ButtonStyle.green, custom_id="ssu_1")
    async def button_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ssu(interaction, "Casual", "> This mode is for a chill and relaxing roleplay or just no roleplay.")

    @discord.ui.button(label="Semi-Serious", style=discord.ButtonStyle.blurple, custom_id="ssu_2")
    async def button_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ssu(interaction, "Semi-Serious", "> More stricter than Casual, with a few limitations but still chill roleplay.")

    @discord.ui.button(label="Serious", style=discord.ButtonStyle.red, custom_id="ssu_3")
    async def button_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ssu(interaction, "Serious", "> This mode is for RP demons 😈")


async def ssu_command(ctx):
    if not is_allowed_ssu_staff(ctx.author):
        return

    embed = discord.Embed(
        title="Server Start Up Mode Picker",
        description="Choose below which mode you are SSUing on",
        color=embed_color
    )

    try:
        await ctx.message.delete()
    except:
        pass

    try:
        await ctx.author.send(embed=embed, view=SSUPollView(ctx.guild))
    except discord.Forbidden:
        await ctx.send("I couldn't DM you. Please enable DMs from server members.", delete_after=3)

def get_next_ticket_name(guild, prefix, username):
    safe_name = re.sub(r"[^a-z0-9-]", "", username.lower())

    base_name = f"{prefix}-{safe_name}"
    ticket_name = base_name
    counter = 1

    while discord.utils.get(guild.text_channels, name=ticket_name):
        ticket_name = f"{base_name}-{counter}"
        counter += 1

    return ticket_name

class GeneralSupportModal(discord.ui.Modal, title="General Support"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )

    issue = discord.ui.TextInput(
        label="What is your issue?",
        style=discord.TextStyle.paragraph,
        required=True
    ) 
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1300119792241475604
        CATEGORY_ID = 1389925525312507924

        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return

        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            member = interaction.user
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return

            print(f"Guild: {guild}")
            print(f"Staff role: {staff}")
            print(f"Staff role ID: {STAFF_ROLE_ID}")
            print(f"Category ID: {CATEGORY_ID}")
            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    use_application_commands=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    send_polls=True,
                    use_application_commands=True,
                )
            } 

            category = guild.get_channel(CATEGORY_ID)

            ticket_name = get_next_ticket_name(
                guild,
                "general",
                interaction.user.name
            )
            existing_ticket = None

            for channel in category.text_channels:
                if channel.name.startswith(f"general-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    existing_ticket = channel
                    break

            if existing_ticket:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing_ticket.mention}",
                    ephemeral=True
                )
                return
            ticket_channel = await guild.create_text_channel(
                    name=ticket_name,
                    category=category,
                    overwrites=overwrites
            )

            embed = discord.Embed(
                title="General Support",
                description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒 button.",
                color=embed_color)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed1 = discord.Embed(
                title="Report Details",
                color=embed_color)
            embed1.add_field(name="What is your Roblox username?", value=f'```{self.roblox.value}```', inline=False)
            embed1.add_field(name="What is your issue?", value=f'```{self.issue.value}```', inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            view = discord.ui.View()

            close_button = discord.ui.Button(
                    label="Close and Log",
                    style=discord.ButtonStyle.red,
                    emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.response.defer(ephemeral=True)
                

                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.followup.send(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                    return

                await interaction.followup.send(embed=embed)

                # Prevent double-clicks firing multiple closes
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return

                log_channel = bot.get_channel(channel_log_ticket)
                if log_channel is None:
                    await interaction.followup.send(
                        "Log channel not found.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed.set_author(
                    name=embed_author_name["name"],
                    icon_url=embed_author_icon["icon_url"]
                )
                embed.set_footer(
                    text=embed_footer_text["text"],
                    icon_url=embed_footer_icon["icon_url"]
                )

                # No temp file on disk → no WinError 32
                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed)

                try:
                    await interaction.channel.delete()
                except discord.NotFound:
                    pass  # already deleted by a concurrent close
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete ticket channel: {e}")

            close_button.callback = close_callback
            view.add_item(close_button)
            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )
            await ticket_channel.send(content=f"{member.mention} <@&{STAFF_ROLE_ID}>", embeds=[embed, embed1], view=view)
        finally: 
            active_ticket_creations.discard(member.id)

class PartnershipSupportModal(discord.ui.Modal, title="Partnership Support"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )

    group = discord.ui.TextInput(
        label="What is the name of your group?",
        style=discord.TextStyle.short,
        required=True
    )
    members = discord.ui.TextInput(
        label="How many members does your group have?",
        style=discord.TextStyle.short,
        required=True
    )
    type = discord.ui.TextInput(
        label="What type of group is your group?", 
        style=discord.TextStyle.short,
        required=True)
    
    owner = discord.ui.TextInput(
        label="Are you the group owner?",
        placeholder = "If not, provide owner's username or ID",
        style=discord.TextStyle.short,
        required=True
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1363831936153555195
        CATEGORY_ID = 1389925682678730782
        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return
        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            member = interaction.user
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return
            
            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    use_application_commands=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    send_polls=True,
                    use_application_commands=True,
                )
            } 

            category = guild.get_channel(CATEGORY_ID)

            ticket_name = get_next_ticket_name(
            guild,
            "partnership",
            interaction.user.name
            )
            existing_ticket = None

            for channel in category.text_channels:
                if channel.name.startswith(f"partnership-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    existing_ticket = channel
                    break

            if existing_ticket:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing_ticket.mention}",
                    ephemeral=True
                )
                return
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )
        
            embed = discord.Embed(
                title="Partnership Support",
                description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒   button.",
                color=embed_color)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed1 = discord.Embed(
                title="Report Details",
                color=embed_color)
            embed1.add_field(name="What is your Roblox username?", value=f'```{self.roblox.value}```', inline=False)
            embed1.add_field(name="What is the name of your group?", value=f'```{self.group.value}```', inline=False)
            embed1.add_field(name="How many members does your group have?", value=f'```{self.members.value}```', inline=False)
            embed1.add_field(name="What type of group is your group?", value=f'```{self.type.value}```', inline=False)
            embed1.add_field(name="Are you the group owner?", value=f'```{self.owner.value}```', inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            view = discord.ui.View()

            close_button = discord.ui.Button(
                    label="Close and Log",
                    style=discord.ButtonStyle.red,
                    emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.response.defer(ephemeral=True)

                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.followup.send(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                    return

                await interaction.followup.send(embed=embed)

                # Prevent double-clicks firing multiple closes
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return

                log_channel = bot.get_channel(channel_log_ticket)
                if log_channel is None:
                    await interaction.followup.send(
                        "Log channel not found.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed.set_author(
                    name=embed_author_name["name"],
                    icon_url=embed_author_icon["icon_url"]
                )
                embed.set_footer(
                    text=embed_footer_text["text"],
                    icon_url=embed_footer_icon["icon_url"]
                )

                # No temp file on disk → no WinError 32
                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed)

                try:
                    await interaction.channel.delete()
                except discord.NotFound:
                    pass  # already deleted by a concurrent close
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete ticket channel: {e}")

            close_button.callback = close_callback
            view.add_item(close_button)
            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )
            await ticket_channel.send(content=f"{member.mention} <@&{STAFF_ROLE_ID}>", embeds=[embed, embed1], view=view)  
        finally: 
            active_ticket_creations.discard(member.id)


class InGameReportsModal(discord.ui.Modal, title="In-Game Reports"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )
    reported_user = discord.ui.TextInput(
        label="What is the username of the offender?",
        style=discord.TextStyle.short,
        required=True
    )
    reason = discord.ui.TextInput(
        label="What is the reason for your report?",
        style=discord.TextStyle.paragraph,
        required=True,
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1300124748235280395
        CATEGORY_ID = 1509601039412625439
        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return

        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return

            # Check for existing ticket
            for channel in category.text_channels:
                if channel.name.startswith(f"report-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}",
                        ephemeral=True
                    )
                    return

            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                )
            }

            ticket_name = get_next_ticket_name(guild, "report", interaction.user.name)

            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )

            embed = discord.Embed(
                title="In-Game Report",
                description="Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒 button.",
                color=embed_color
            )
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            embed1 = discord.Embed(title="Report Details", color=embed_color)
            embed1.add_field(name="Roblox Username", value=f"```{self.roblox.value}```", inline=False)
            embed1.add_field(name="Reported User", value=f"```{self.reported_user.value}```", inline=False)
            embed1.add_field(name="Reason", value=f"```{self.reason.value}```", inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            # === Create the view + button properly ===
            view = discord.ui.View(timeout=None)

            close_button = discord.ui.Button(
                label="Close and Log",
                style=discord.ButtonStyle.red,
                emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=True)

                # Disable buttons
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                embed_closed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed_closed)

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send("Transcript couldn't be generated.", ephemeral=True)
                    return

                log_channel = bot.get_channel(channel_log_ticket)
                if log_channel is None:
                    await interaction.followup.send("Log channel not found.", ephemeral=True)
                    return

                embed_log = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed_log.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed_log.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed_log)

                try:
                    await interaction.channel.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

            close_button.callback = close_callback
            view.add_item(close_button)

            # Send everything
            await ticket_channel.send(
                content=f"{member.mention} <@&{STAFF_ROLE_ID}>",
                embeds=[embed, embed1],
                view=view
            )

            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )

        finally:
            active_ticket_creations.discard(member.id)

class GeneralSupportModalH(discord.ui.Modal, title="General Support"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )

    issue = discord.ui.TextInput(
        label="What is your issue?",
        style=discord.TextStyle.paragraph,
        required=True
    ) 
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1346192810998501396
        CATEGORY_ID = 1383014801399349268

        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return

        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            member = interaction.user
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return

            print(f"Guild: {guild}")
            print(f"Staff role: {staff}")
            print(f"Staff role ID: {STAFF_ROLE_ID}")
            print(f"Category ID: {CATEGORY_ID}")
            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    use_application_commands=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    send_polls=True,
                    use_application_commands=True,
                )
            } 

            category = guild.get_channel(CATEGORY_ID)

            ticket_name = get_next_ticket_name(
                guild,
                "general",
                interaction.user.name
            )
            existing_ticket = None

            for channel in category.text_channels:
                if channel.name.startswith(f"general-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    existing_ticket = channel
                    break

            if existing_ticket:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing_ticket.mention}",
                    ephemeral=True
                )
                return
            ticket_channel = await guild.create_text_channel(
                    name=ticket_name,
                    category=category,
                    overwrites=overwrites
            )

            embed = discord.Embed(
                title="General Support",
                description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒 button.",
                color=embed_color)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed1 = discord.Embed(
                title="Report Details",
                color=embed_color)
            embed1.add_field(name="What is your Roblox username?", value=f'```{self.roblox.value}```', inline=False)
            embed1.add_field(name="What is your issue?", value=f'```{self.issue.value}```', inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            view = discord.ui.View()

            close_button = discord.ui.Button(
                    label="Close and Log",
                    style=discord.ButtonStyle.red,
                    emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.response.defer(ephemeral=True)
                

                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.followup.send(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                    return

                await interaction.followup.send(embed=embed)

                # Prevent double-clicks firing multiple closes
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return

                log_channel = bot.get_channel(channel_log_ticket_hub)
                if log_channel is None:
                    await interaction.followup.send(
                        "Log channel not found.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed.set_author(
                    name=embed_author_name["name"],
                    icon_url=embed_author_icon["icon_url"]
                )
                embed.set_footer(
                    text=embed_footer_text["text"],
                    icon_url=embed_footer_icon["icon_url"]
                )

                # No temp file on disk → no WinError 32
                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed)

                try:
                    await interaction.channel.delete()
                except discord.NotFound:
                    pass  # already deleted by a concurrent close
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete ticket channel: {e}")

            close_button.callback = close_callback
            view.add_item(close_button)
            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )
            await ticket_channel.send(content=f"{member.mention} <@&{STAFF_ROLE_ID}>", embeds=[embed, embed1], view=view)
        finally: 
            active_ticket_creations.discard(member.id)

class StaffReportModalH(discord.ui.Modal, title="Staff Report"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )
    staff_member = discord.ui.TextInput(
        label="Which staff member are you reporting?",
        style=discord.TextStyle.short,
        placeholder="Codename/username, anything that helps us identify the staff member.",
        required=True
    )
    reason = discord.ui.TextInput(
        label="Why are you reporting this staff member?",
        style=discord.TextStyle.paragraph,
        required=True
    )
    actions = discord.ui.TextInput(
        label="What actions do you wish that they recieve?", 
        style=discord.TextStyle.paragraph,
        required=True)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1523264806012850337
        CATEGORY_ID = 1526569434901119056
        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return
        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            member = interaction.user
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return
            
            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    use_application_commands=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    send_tts_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                    send_voice_messages=True,
                    send_polls=True,
                    use_application_commands=True,
                )
            } 

            category = guild.get_channel(CATEGORY_ID)

            ticket_name = get_next_ticket_name(
            guild,
            "staff-report",
            interaction.user.name
            )
            existing_ticket = None

            for channel in category.text_channels:
                if channel.name.startswith(f"staff-report-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    existing_ticket = channel
                    break

            if existing_ticket:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing_ticket.mention}",
                    ephemeral=True
                )
                return
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )
        
            embed = discord.Embed(
                title="Staff Report",
                description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒   button.",
                color=embed_color)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed1 = discord.Embed(
                title="Report Details",
                color=embed_color)
            embed1.add_field(name="What is your Roblox username?", value=f'```{self.roblox.value}```', inline=False)
            embed1.add_field(name="Which staff member are you reporting?", value=f'```{self.staff_member.value}```', inline=False)
            embed1.add_field(name="Why are you reporting this staff member?", value=f'```{self.reason.value}```', inline=False)
            embed1.add_field(name="What actions do you want to be taken against this staff member?", value=f'```{self.actions.value}```', inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            view = discord.ui.View()

            close_button = discord.ui.Button(
                    label="Close and Log",
                    style=discord.ButtonStyle.red,
                    emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.response.defer(ephemeral=True)

                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.followup.send(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                    return

                await interaction.followup.send(embed=embed)

                # Prevent double-clicks firing multiple closes
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return

                log_channel = bot.get_channel(1527607159020589066)
                if log_channel is None:
                    await interaction.followup.send(
                        "Log channel not found.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed.set_author(
                    name=embed_author_name["name"],
                    icon_url=embed_author_icon["icon_url"]
                )
                embed.set_footer(
                    text=embed_footer_text["text"],
                    icon_url=embed_footer_icon["icon_url"]
                )

                # No temp file on disk → no WinError 32
                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed)

                try:
                    await interaction.channel.delete()
                except discord.NotFound:
                    pass  # already deleted by a concurrent close
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete ticket channel: {e}")

            close_button.callback = close_callback
            view.add_item(close_button)
            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )
            await ticket_channel.send(content=f"{member.mention} <@&{STAFF_ROLE_ID}>", embeds=[embed, embed1], view=view)  
        finally: 
            active_ticket_creations.discard(member.id)


class FactionReportsModal(discord.ui.Modal, title="Faction Reports"):
    faction_name = discord.ui.TextInput(
        label="Faction Name",
        required=True,
        min_length=3,
        max_length=32
    )
    faction_id = discord.ui.TextInput(
        label="Faction ID",
        style=discord.TextStyle.short,
        required=True
    )
    reason = discord.ui.TextInput(
        label="Reason for the report",
        style=discord.TextStyle.paragraph,
        required=True,
    )
    proof = discord.ui.TextInput(
        label="Do you have valid proof?",
        style=discord.TextStyle.paragraph,
        placeholder="Yes/No",
        required=True,
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        STAFF_ROLE_ID = 1346192810998501396
        CATEGORY_ID = 1383014862300647464
        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return

        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return

            # Check for existing ticket
            for channel in category.text_channels:
                if channel.name.startswith(f"report-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())}"):
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}",
                        ephemeral=True
                    )
                    return

            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                )
            }

            ticket_name = get_next_ticket_name(guild, "report", interaction.user.name)

            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )

            embed = discord.Embed(
                title="Faction Report",
                description="Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒 button.",
                color=embed_color
            )
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            embed1 = discord.Embed(title="Report Details", color=embed_color)
            embed1.add_field(name="Faction Name", value=f"```{self.faction_name.value}```", inline=False)
            embed1.add_field(name="Faction ID", value=f"```{self.faction_id.value}```", inline=False)
            embed1.add_field(name="Reason", value=f"```{self.reason.value}```", inline=False)
            embed1.add_field(name="Proof", value=f"```{self.proof.value}```", inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            # === Create the view + button properly ===
            view = discord.ui.View(timeout=None)

            close_button = discord.ui.Button(
                label="Close and Log",
                style=discord.ButtonStyle.red,
                emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=True)

                # Disable buttons
                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                embed_closed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed_closed)

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send("Transcript couldn't be generated.", ephemeral=True)
                    return

                log_channel = bot.get_channel(channel_log_ticket_hub)
                if log_channel is None:
                    await interaction.followup.send("Log channel not found.", ephemeral=True)
                    return

                embed_log = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed_log.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed_log.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed_log)

                try:
                    await interaction.channel.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

            close_button.callback = close_callback
            view.add_item(close_button)

            # Send everything
            await ticket_channel.send(
                content=f"{member.mention} <@&{STAFF_ROLE_ID}>",
                embeds=[embed, embed1],
                view=view
            )

            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )

        finally:
            active_ticket_creations.discard(member.id)

class AppealSupportModal(discord.ui.Modal, title="Faction Appeals"):
    faction_name = discord.ui.TextInput(
        label="Faction Name",
        required=True,
        min_length=3,
        max_length=32
    )
    faction_id = discord.ui.TextInput(
        label="Faction ID",
        style=discord.TextStyle.short,
        required=True
    )
    punishment_id = discord.ui.TextInput(
        label="Punishment ID",
        style=discord.TextStyle.short,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        STAFF_ROLE_ID = 1346192810998501396
        CATEGORY_ID = 1383014709732708414
        member = interaction.user

        if member.id in active_ticket_creations:
            await interaction.followup.send(
                "You are already creating a ticket. Please wait a moment.",
                ephemeral=True
            )
            return

        # ===== Validate punishment ID first =====
        punishments = load_punishments()
        found = None

        for entry in punishments:
            if entry["punishment_id"] == self.punishment_id.value:
                found = entry
                break

        if not found:
            await interaction.followup.send(
                f"Punishment ID `{self.punishment_id.value}` not found.",
                ephemeral=True
            )
            return

        if found.get("appealable") is False:
            await interaction.followup.send(
                "This punishment is marked as **unappealable**. If you wish to discuss that punishment or believe this is a mistake, please open a general support ticket.",
                ephemeral=True
            )
            return

        if found.get("status") != "active":
            await interaction.followup.send(
                f"This punishment is not active (current status: `{found.get('status')}`).",
                ephemeral=True
            )
            return

        active_ticket_creations.add(member.id)
        try:
            guild = interaction.guild
            everyone = guild.default_role
            staff = guild.get_role(STAFF_ROLE_ID)
            category = guild.get_channel(CATEGORY_ID)

            if staff is None or category is None:
                await interaction.followup.send(
                    "Staff role or category is missing. Contact an admin.",
                    ephemeral=True
                )
                return

            # Check for existing ticket
            safe_name = re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())
            for channel in category.text_channels:
                if channel.name.startswith(f"appeal-{safe_name}"):
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}",
                        ephemeral=True
                    )
                    return

            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                ),
                staff: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    add_reactions=True,
                )
            }

            ticket_name = get_next_ticket_name(guild, "appeal", interaction.user.name)

            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )

            # Welcome embed
            embed = discord.Embed(
                title="Faction Appeal",
                description="Welcome. Staff will be with you shortly. In the meantime, please explain why you are appealing this punishment. If you wish to close the ticket, click the 🔒 button.",
                color=embed_color
            )
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            # User submitted details
            embed1 = discord.Embed(title="Appeal Details", color=embed_color)
            embed1.add_field(name="Faction Name", value=f"```{self.faction_name.value}```", inline=False)
            embed1.add_field(name="Faction ID", value=f"```{self.faction_id.value}```", inline=False)
            embed1.add_field(name="Punishment ID", value=f"```{self.punishment_id.value}```", inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            # Punishment info embed
            embed2 = discord.Embed(
                title=f"Punishment Info - `{self.punishment_id.value}`",
                color=embed_color
            )
            embed2.add_field(name="Faction Name", value=found.get("faction_name", "N/A"), inline=True)
            embed2.add_field(name="Punishment", value=found.get("punishment", "N/A"), inline=True)
            embed2.add_field(name="Status", value=str(found.get("status", "N/A")).upper(), inline=True)
            embed2.add_field(name="Appealable", value="Yes" if found.get("appealable") else "No", inline=True)
            embed2.add_field(name="Reason", value=found.get("reason", "N/A"), inline=False)
            embed2.add_field(name="Proof", value=found.get("proof", "N/A"), inline=False)
            embed2.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])

            # Close button
            view = discord.ui.View(timeout=None)
            close_button = discord.ui.Button(
                label="Close and Log",
                style=discord.ButtonStyle.red,
                emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                if interaction.user != member and (staff is None or staff not in interaction.user.roles):
                    await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=True)

                for item in view.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except Exception:
                    pass

                embed_closed = discord.Embed(
                    title="Ticket Closed",
                    description=f"Ticket closed by {interaction.user.mention}. This ticket will be deleted in a few seconds.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed_closed)

                transcript = await chat_exporter.export(interaction.channel, limit=None)
                if transcript is None:
                    await interaction.followup.send("Transcript couldn't be generated.", ephemeral=True)
                    return

                log_channel = bot.get_channel(channel_log_ticket)
                if log_channel is None:
                    await interaction.followup.send("Log channel not found.", ephemeral=True)
                    return

                embed_log = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed_log.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed_log.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

                file = discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"{interaction.channel.name}.html"
                )
                await log_channel.send(file=file, embed=embed_log)

                try:
                    await interaction.channel.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

            close_button.callback = close_callback
            view.add_item(close_button)

            await ticket_channel.send(
                content=f"{member.mention} <@&{STAFF_ROLE_ID}>",
                embeds=[embed, embed1, embed2],
                view=view
            )

            await interaction.followup.send(
                f"Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )

        finally:
            active_ticket_creations.discard(member.id)

def get_excluded_role_ids(config_data=None):
    if config_data is None:
        config_data = load_config()
    raw = config_data.get("EXCLUDED_ROLE_IDS", [])
    if not raw:
        return set()
    if isinstance(raw, list):
        return {int(r) for r in raw if r not in (None, "")}
    return {int(raw)}

class SupportTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.select(
        custom_id="support_ticket_select",
        placeholder="Select a ticket category...",
        options=[
            discord.SelectOption(
                label="⚒️  |  General Support",
                value="general_support",
                description="If you have any general questions or inquiries, please select this option."
            ),
            discord.SelectOption(
                label="🤝  |  Partnership Support",
                value="partnership_support",
                description="If you wish to partnership with Area - 14 or discuss partnership collaborations, select this option."
            ),
            discord.SelectOption(
                label="🎮  |  In-Game Reports",
                value="in_game_reports",
                description="If you wish to report someone rule-breaking in-game, please select this option."
            ),
        ],
    )
    async def support_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_value = select.values[0]

        if selected_value == "general_support":
            await interaction.response.send_modal(GeneralSupportModal())

        elif selected_value == "partnership_support":
            await interaction.response.send_modal(PartnershipSupportModal())

        elif selected_value == "in_game_reports":
            await interaction.response.send_modal(InGameReportsModal())
        else:
            await interaction.followup.send(
                "```⚠︎⚠︎⚠︎ ERROR. CONTACT RGSRANDOM. ⚠︎⚠︎⚠︎```.",
                ephemeral=True,
            )
            return

class StaffTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.select(
        custom_id="staff_ticket_select",
        placeholder="Select a ticket category...",
        options=[
            discord.SelectOption(
                label="⚒️  |  General Support",
                value="general_support",
                description="If you have any general questions or inquiries, please select this option."
            ),
            discord.SelectOption(
                label="👮  |  Staff Report",
                value="staff_report",
                description="Report a faction management member breaking rules."
            ),
            discord.SelectOption(
                label="📋  |  Faction Report",
                value="faction_report",
                description="If you wish to report factions breaking regulations, select this option."
            ),
            discord.SelectOption(
                label="🔓  |  Appeal Support",
                value="appeal_support",
                description="If you wish to appeal a faction infraction, select this option."
            ),
        ],
    )
    async def support_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_value = select.values[0]

        if selected_value == "general_support":
            await interaction.response.send_modal(GeneralSupportModalH())

        elif selected_value == "staff_report":
            await interaction.response.send_modal(StaffReportModalH())

        elif selected_value == "faction_report":
            await interaction.response.send_modal(FactionReportsModal())

        elif selected_value == "appeal_support":
            await interaction.response.send_modal(AppealSupportModal())
        else:
            await interaction.followup.send(
                "```⚠︎⚠︎⚠︎ ERROR. CONTACT RGSRANDOM. ⚠︎⚠︎⚠︎```.",
                ephemeral=True,
            )
            return

async def ticket_commandmain(ctx):
    if not is_allowed_ticket_staff(ctx.author):
        return

    embed = info_embed(
        title="Support Ticket",
        description="**Welcome to the support ticket system! Please select the type of support you need from the options below.**",
        requested_by=ctx.author,
    )

    embed.add_field(name="General Support", value="If you have any general questions or inquiries, please select this option.", inline=False)
    embed.add_field(name="Partnership Support", value="If you wish to partnership with Area - 14 or discuss partnership collaborations, select this option.", inline=False)
    embed.add_field(name="In-Game Reports", value="If you wish to report someone rule-breaking in-game, please select this option.", inline=False)
    await ctx.send(embed=embed, view=SupportTicketView())

async def ticket_commandhub(ctx):
    if not is_allowed_ticket_staff(ctx.author):
        return

    embed = info_embed(
        title="Support Ticket",
        description="**Welcome to the support ticket system! Please select the type of support you need from the options below.**",
        requested_by=ctx.author,
    )

    embed.add_field(name="General Support", value="If you have any general questions or inquiries, please select this option.", inline=False)
    embed.add_field(name="Staff Report", value="If you wish to report a staff member breaking rules, select this option.", inline=False)
    embed.add_field(name="Faction Report", value="If you wish to report factions breaking regulations, select this option.", inline=False)
    embed.add_field(name="Appeal Support", value="If you wish to appeal a faction infraction, select this option.", inline=False)
    await ctx.send(embed=embed, view=StaffTicketView())


COG_MODULES = (
    "cogs.admin",
    "cogs.moderation",
    "cogs.ssu",
    "cogs.tickets",
    "cogs.punishments",
    "cogs.help",
)


async def setup_hook():
    for module_name in COG_MODULES:
        await bot.load_extension(module_name)


bot.setup_hook = setup_hook

if __name__ == "__main__":
    if not token:
        logger.error("❌ BOT_TOKEN not found! Make sure your .env file exists and contains BOT_TOKEN.")
        logger.info("   Create a .env file with: BOT_TOKEN=your_token_here")
        sys.exit(1)
    
    logger.info("=" * 50)
    logger.info("🚀 Starting Discord Role Sync Bot...")
    logger.info("=" * 50)
    
    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("⏹️  Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)