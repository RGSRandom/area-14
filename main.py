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
import chat_exporter

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


ALLOWED_CONTROL_USER_IDS = {1020581214077333525, 1241045030274203659}
_sync_enabled = True


def is_controlled_user(user_id):
    return user_id in ALLOWED_CONTROL_USER_IDS


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
        bot.add_view(SupportTicketView())
        views_loaded = True

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
            logger.info(f"Skipping {target_member.name} because they have an excluded role")
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
                if role:
                    await target_member.add_roles(role)
                    channel = bot.get_channel(channel_log_id)

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
        logger.info(f"[DEBUG] Event guild {after.guild.id} not in configured source guilds — ignoring")
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
                    title="🔒  |  ERROR",
                    description=f"Error syncing {target_member.mention}: {e}",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=after.name, icon_url=after.display_avatar.url)               
                logger.error(f"Error syncing member {target_member.name}: {e}", exc_info=True)

    # Check for roles that were removed
    removed_role_ids = before_role_ids - after_role_ids
    logger.info(f"[DEBUG] Removed role IDs: {sorted(removed_role_ids)}")
    for removed_role_id in removed_role_ids:
        logger.info(f"[DEBUG] Processing removed source role id: {removed_role_id}")
        target_role_ids = role_mapping.get(removed_role_id)
        if not target_role_ids:
            logger.info(f"[DEBUG] No mapping for removed source role {removed_role_id}")
            continue  # No mapping for this role

        for target_role_id in target_role_ids:
            try:
                target_role = target_guild.get_role(target_role_id)
                if not target_role:
                    logger.error(f"[DEBUG] Target role {target_role_id} not found")
                    continue

                logger.info(f"[DEBUG] Attempting to remove role '{target_role.name}' ({target_role.id}) from user {after.id}")
                await target_member.remove_roles(target_role)
                channel = bot.get_channel(channel_log_id)

                embed = discord.Embed(
                    title="🗑️  |  ROLE REMOVED",
                    description=f"Removed role <@&{target_role.id}> from user <@{after.id}>.",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=after.name, icon_url=after.display_avatar.url)
                await channel.send(embed=embed)
                logger.info(f"🗑️ Removed role '{target_role.name}' from {after.name} in target server")

            except Exception as e:
                logger.error(f"Error removing role for removed role id {removed_role_id} -> target {target_role_id}: {e}", exc_info=True)

# --- Manual `a!sync` command and concurrency guard ---
# Track active syncs per target guild to prevent cross-source conflicts
active_sync_targets = set()
@bot.command(name="debug")
async def debug(ctx):
    if ctx.author.id not in ALLOWED_CONTROL_USER_IDS:
        return
    config_data = load_config()

    embed = discord.Embed(
        title="🔧 Bot Debug",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Bot",
        value=f"{bot.user}\nID: `{bot.user.id}`",
        inline=False
    )

    embed.add_field(
        name="Latency",
        value=f"{round(bot.latency * 1000)} ms",
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
        value="✅ Yes" if is_sync_enabled() else "❌ No",
        inline=True
    )

    embed.add_field(
        name="Periodic Sync",
        value="🟢 Running" if sync_roles.is_running() else "🔴 Stopped",
        inline=True
    )

    embed.add_field(
        name="Test Mode",
        value="✅ Enabled" if is_test_mode_enabled(config_data) else "❌ Disabled",
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

    embed.set_footer(text=f"PID: {os.getpid()}")

    await ctx.send(embed=embed)

@bot.command(name="sync")
async def perform_manual_sync(ctx):
    logger.warning(f"perform_manual_sync called from message id={getattr(ctx, 'id', None)} channel={getattr(ctx, 'channel', None)}")
    """Perform a one-off sync and update a status message every 2 seconds.
    This version fetches full member lists from source and target guilds to avoid cache misses.
    """
    if not is_sync_enabled():
        if ctx:
            await ctx.channel.send(
                embed=discord.Embed(
                    title="Sync Paused",
                    description="Sync is currently paused. Use a!start first.",
                    color=discord.Color.orange()
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
                              color=discord.Color.red())
        if ctx:
            await ctx.channel.send(embed=embed)
        else:
            logger.error("Startup sync: one or more configured servers are not available to the bot.")
        return

    if TARGET_GUILD_ID in active_sync_targets:
        embed = discord.Embed(title="Sync Already Running",
                              description="A sync is already running for the target server; cannot start another.",
                              color=discord.Color.orange())
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
            pass

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
        active_sync_targets.discard(TARGET_GUILD_ID)

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
        STAFF_ROLE_ID = 1363831936153555195
        CATEGORY_ID = 1389925525312507924

        guild = interaction.guild
        everyone = guild.default_role
        member = interaction.user
        staff = guild.get_role(STAFF_ROLE_ID)

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

        await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=overwrites
        )
        ticket_name = get_next_ticket_name(
        guild,
        "partnership",
        interaction.user.name
        )
        
        ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
        )

        embed = discord.Embed(
            title="In-Game Report",
            description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒button.",
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
                label="Close",
                style=discord.ButtonStyle.red,
                emoji="🔒"
        )

        async def close_callback(interaction: discord.Interaction):
            if interaction.user != member and staff not in interaction.user.roles:
                await interaction.response.send_message(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                return
            transcript = await chat_exporter.export(
                    interaction.channel,
                    limit=None
                )
            if transcript is None:
                    await interaction.response.send_message(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return
            with open(f"{interaction.channel.name}.html", "w", encoding="utf-8") as f:
                    f.write(transcript)
            log_channel = bot.get_channel(channel_log_ticket)

            filename = f"{interaction.channel.name}.html"

            with open(filename, "w", encoding="utf-8") as f:
                    f.write(transcript)

            embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])
            await log_channel.send(
                    embed=embed,
                    file=discord.File(filename)
                )

            os.remove(filename)
            await interaction.channel.delete()

        close_button.callback = close_callback
        view.add_item(close_button)
        await ticket_channel.send(content=f"{member.mention}", embeds=[embed, embed1], view=view)


class PartnershipSupportModal(discord.ui.Modal, title="Partnership Support"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )

    group = discord.ui.TextInput(
        label="What is the name of your group?",
        style=discord.TextStyle.paragraph,
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
        STAFF_ROLE_ID = 1363831936153555195
        CATEGORY_ID = 1389925682678730782

        guild = interaction.guild
        everyone = guild.default_role
        member = interaction.user
        staff = guild.get_role(STAFF_ROLE_ID)
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
        
        ticket_channel = await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=overwrites
        )
        embed = discord.Embed(
            title="In-Game Report",
            description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒button.",
            color=embed_color)
        embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
        embed1 = discord.Embed(
            title="Report Details",
            color=embed_color)
        embed1.add_field(name="What is your Roblox username?", value=f'```{self.roblox.value}```', inline=False)
        embed1.add_field(name="What is the name of your group?", value=f'```{self.group_name.value}```', inline=False)
        embed1.add_field(name="What type of group is your group?", value=f'```{self.type.value}```', inline=False)
        embed1.add_field(name="Are you the group owner?", value=f'```{self.owner.value}```', inline=False)
        embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

        view = discord.ui.View()

        close_button = discord.ui.Button(
                label="Close",
                style=discord.ButtonStyle.red,
                emoji="🔒"
        )

        async def close_callback(interaction: discord.Interaction):
            if interaction.user != member and staff not in interaction.user.roles:
                await interaction.response.send_message(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                return
            transcript = await chat_exporter.export(
                    interaction.channel,
                    limit=None
                )
            if transcript is None:
                    await interaction.response.send_message(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return
            with open(f"{interaction.channel.name}.html", "w", encoding="utf-8") as f:
                    f.write(transcript)
            log_channel = bot.get_channel(channel_log_ticket)

            filename = f"{interaction.channel.name}.html"

            with open(filename, "w", encoding="utf-8") as f:
                    f.write(transcript)

            embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])
            await log_channel.send(
                    embed=embed,
                    file=discord.File(filename)
                )

            os.remove(filename)
            await interaction.channel.delete()

        close_button.callback = close_callback
        view.add_item(close_button)
        await ticket_channel.send(content=f"{member.mention}", embeds=[embed, embed1], view=view)       

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
            STAFF_ROLE_ID = 1363831936153555195
            CATEGORY_ID = 1509601039412625439

            guild = interaction.guild
            everyone = guild.default_role
            member = interaction.user
            staff = guild.get_role(STAFF_ROLE_ID)

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
                "report",
                interaction.user.name
            )
            
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )

            embed = discord.Embed(
                title="In-Game Report",
                description=f"Welcome. Staff will be with you shortly. In the meantime, please explain the issue thoroughly. If you wish to close the ticket, click the 🔒button.",
                color=embed_color)
            embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
            embed1 = discord.Embed(
                title="Report Details",
                color=embed_color)
            embed1.add_field(name="Roblox Username", value=f'```{self.roblox.value}```', inline=False)
            embed1.add_field(name="Reported User", value=f'```{self.reported_user.value}```', inline=False)
            embed1.add_field(name="Reason", value=f'```{self.reason.value}```', inline=False)
            embed1.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])

            view = discord.ui.View()

            close_button = discord.ui.Button(
                label="Close",
                style=discord.ButtonStyle.red,
                emoji="🔒"
            )

            async def close_callback(interaction: discord.Interaction):
                if interaction.user != member and staff not in interaction.user.roles:
                    await interaction.response.send_message(
                        "You cannot close this ticket.",
                        ephemeral=True
                    )
                    return
                transcript = await chat_exporter.export(
                    interaction.channel,
                    limit=None
                )
                if transcript is None:
                    await interaction.response.send_message(
                        "Transcript couldn't be generated.",
                        ephemeral=True
                    )
                    return
                with open(f"{interaction.channel.name}.html", "w", encoding="utf-8") as f:
                    f.write(transcript)
                log_channel = bot.get_channel(channel_log_ticket)

                filename = f"{interaction.channel.name}.html"

                with open(filename, "w", encoding="utf-8") as f:
                    f.write(transcript)

                embed = discord.Embed(
                    title="Ticket Logged",
                    description=f"Ticket closed by {interaction.user.mention}. Transcript attached.",
                    color=embed_color
                )
                embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
                embed.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])
                await log_channel.send(
                    embed=embed,
                    file=discord.File(filename)
                )

                os.remove(filename)
                await interaction.channel.delete()

            close_button.callback = close_callback
            view.add_item(close_button)
            await ticket_channel.send(content=f"{member.mention}", embeds=[embed, embed1], view=view)

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
            await interaction.response.send_message(
                "```⚠︎⚠︎⚠︎ ERROR. CONTACT RGSRANDOM. ⚠︎⚠︎⚠︎```.",
                ephemeral=True,
            )
            return

@bot.command(name='&^V1mticket')
async def ticket_commandmain(ctx):
    embed = discord.Embed(
        title="⚒️ | Support Ticket",
        description="Welcome to the support ticket system! Please select the type of support you need from the options below.",
        color=embed_color,
    )

    embed.add_field(name="⚒️  |  General Support", value="If you have any general questions or inquiries, please select this option.", inline=False)
    embed.add_field(name="🤝  |  Partnership Support", value="If you wish to partnership with Area - 14 or discuss partnership collaborations, select this option.", inline=False)
    embed.add_field(name="🎮  |  In-Game Reports", value="If you wish to report someone rule-breaking in-game, please select this option.", inline=False)
    embed.set_author(name=embed_author_name["name"], icon_url=embed_author_icon["icon_url"])
    embed.set_footer(text=embed_footer_text["text"], icon_url=embed_footer_icon["icon_url"])
    msg = await ctx.send(embed=embed, view=SupportTicketView())

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