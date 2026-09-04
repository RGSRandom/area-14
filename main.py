import discord
from discord.ext import commands, tasks
import logging
from pathlib import Path
from dotenv import load_dotenv
import os
import json
import sys
import asyncio

load_dotenv(dotenv_path=Path(__file__).with_name('.env'))

# Setup logging
log_stream = sys.stdout
if hasattr(log_stream, 'reconfigure'):
    try:
        log_stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

# File handler: keep full timestamp for logfile
log_file_path = Path(__file__).with_name('discord.log').resolve()
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
config_file_path = Path(__file__).with_name('config.json').resolve()
dangerous_perms_path = Path(__file__).with_name('dangerous_perms.json').resolve()

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
    config_path = Path(__file__).with_name('config.json')
    dp_path = Path(__file__).with_name('dangerous_perms.json')
    logger.info(f"[DEBUG] Loaded config from: {config_path.resolve()}")
    logger.info(f"[DEBUG] Loaded dangerous_perms from: {dp_path.resolve()}")
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
    if config_data is None:
        config_data = load_config()
    role_mapping = {}
    for mapping in config_data["role_mappings"]:
        if "source_role_id" not in mapping or "target_role_id" not in mapping:
            raise ValueError("Each role mapping must include source_role_id and target_role_id")
        role_mapping[mapping["source_role_id"]] = mapping["target_role_id"]
    logger.info(f"[DEBUG] build_role_mapping produced {len(role_mapping)} entries")
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

bot = commands.Bot(command_prefix='!', intents=intent, help_command=None)   

@bot.event
async def on_ready():
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

    # Start the sync loop if not already running
    if not sync_roles.is_running():
        sync_roles.start()
        logger.info("🔄 Started periodic role sync (every 30 minutes)")

@tasks.loop(minutes=30)
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
                        should_have_target_roles.add(role_mapping[source_role.id])

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
                    logger.info(f"🗑️ Sync removed '{role.name}' from {target_member.name}")
                    synced_count += 1
            
            # Add roles they should have but don't
            roles_to_add = should_have_target_roles - current_mapped_target_roles
            for role_id in roles_to_add:
                role = target_guild.get_role(role_id)
                if role:
                    await target_member.add_roles(role)
                    logger.info(f"✅ Sync added '{role.name}' to {target_member.name}")
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
        logger.info("[DEBUG] No role changes detected — returning")
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
        # Debug lookup
        logger.info(f"[DEBUG] Processing added source role id: {added_role_id}")
        target_role_id = role_mapping.get(added_role_id)
        if not target_role_id:
            logger.info(f"[DEBUG] No mapping for source role {added_role_id}")
            continue  # No mapping for this role

        try:
            # Get the role to apply
            target_role = target_guild.get_role(target_role_id)
            if not target_role:
                logger.error(f"[DEBUG] Target role {target_role_id} not found")
                continue

            # Check if the target role has dangerous permissions
            dangerous_perms_list = current_dangerous_perms.get("dangerous_permissions", [])
            role_permissions = target_role.permissions

            dangerous_found = []
            for perm in dangerous_perms_list:
                has = getattr(role_permissions, perm, False)
                logger.info(f"[DEBUG] Role '{target_role.name}' perm {perm}: {has}")
                if has:
                    dangerous_found.append(perm)

            if dangerous_found:
                logger.warning(f"🚫 BLOCKED: Role '{target_role.name}' has dangerous permissions: {dangerous_found}")
                logger.warning(f"   User {after.name} was NOT given this role. Edit dangerous_perms.json if needed.")
                continue

            # Apply the role
            logger.info(f"[DEBUG] Attempting to add role '{target_role.name}' ({target_role.id}) to user {after.id}")
            await target_member.add_roles(target_role)
            logger.info(f"✅ Added role '{target_role.name}' to {after.name} in target server")

        except Exception as e:
            logger.error(f"Error adding role for added role id {added_role_id}: {e}", exc_info=True)

    # Check for roles that were removed
    removed_role_ids = before_role_ids - after_role_ids
    logger.info(f"[DEBUG] Removed role IDs: {sorted(removed_role_ids)}")
    for removed_role_id in removed_role_ids:
        logger.info(f"[DEBUG] Processing removed source role id: {removed_role_id}")
        target_role_id = role_mapping.get(removed_role_id)
        if not target_role_id:
            logger.info(f"[DEBUG] No mapping for removed source role {removed_role_id}")
            continue  # No mapping for this role

        try:
            # Get the role to remove
            target_role = target_guild.get_role(target_role_id)
            if not target_role:
                logger.error(f"[DEBUG] Target role {target_role_id} not found")
                continue

            # Remove the role
            logger.info(f"[DEBUG] Attempting to remove role '{target_role.name}' ({target_role.id}) from user {after.id}")
            await target_member.remove_roles(target_role)
            logger.info(f"🗑️ Removed role '{target_role.name}' from {after.name} in target server")

        except Exception as e:
            logger.error(f"Error removing role for removed role id {removed_role_id}: {e}", exc_info=True)

# --- Manual `a!sync` command and concurrency guard ---
# Track active syncs per target guild to prevent cross-source conflicts
active_sync_targets = set()


async def perform_manual_sync(triggering_message):
    """Perform a one-off sync and update a status message every 2 seconds.
    This version fetches full member lists from source and target guilds to avoid cache misses.
    """
    if not is_sync_enabled():
        await triggering_message.channel.send("Sync is currently paused. Use a!start first.")
        return

    config_data = load_config()
    source_guild_ids = get_source_guild_ids(config_data)
    TARGET_GUILD_ID = config_data["TARGET_GUILD_ID"]
    current_dangerous_perms = load_dangerous_perms()

    target_guild = bot.get_guild(TARGET_GUILD_ID)
    source_guilds = [bot.get_guild(sid) for sid in source_guild_ids]

    if not target_guild or any(s is None for s in source_guilds):
        await triggering_message.channel.send("Error: one or more configured guilds are not available to the bot.")
        return

    if TARGET_GUILD_ID in active_sync_targets:
        await triggering_message.channel.send("A sync is already running for the target server; cannot start another.")
        return

    # Mark sync active for this target
    active_sync_targets.add(TARGET_GUILD_ID)

    status_msg = await triggering_message.channel.send("Starting manual sync (fetching members)...")

    # Fetch full member lists to avoid relying on partial cache
    try:
        target_members = [m async for m in target_guild.fetch_members(limit=None)]
    except Exception:
        # Fall back to cached members if fetching fails
        target_members = list(target_guild.members)

    if is_test_mode_enabled(config_data):
        test_user_id = get_test_user_id(config_data)
        if test_user_id is None:
            await triggering_message.channel.send("Test mode is enabled but TEST_USER_ID is missing; manual sync has been disabled.")
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
                await status_msg.edit(content=f"Manual sync: processed {processed}/{total_members} members - {changes} role changes so far")
                await asyncio.sleep(2)
        except Exception:
            pass

    progress_task = asyncio.create_task(progress_updater())

    try:
        role_mapping = build_role_mapping(config_data)
        managed_target_role_ids = get_managed_target_role_ids(config_data)

        for target_member in target_members:
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
                            should_have_target_roles.add(role_mapping[source_role.id])

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
                        logger.info(f"Added '{role.name}' to {target_member.name}")
                        changes += 1

                processed += 1

            except Exception as e:
                logger.error(f"Error syncing member {target_member.name}: {e}")

        stop_progress = True
        await progress_task
        await status_msg.edit(content=f"Manual sync complete: processed {processed}/{total_members} members - {changes} role changes made")

    finally:
        active_sync_targets.discard(TARGET_GUILD_ID)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip().lower()
    if content == 'a!start':
        if not is_controlled_user(message.author.id):
            await message.channel.send("You are not authorized to use this command.")
            return
        set_sync_enabled(True)
        await message.channel.send("Sync started.")
        return

    if content == 'a!stop':
        if not is_controlled_user(message.author.id):
            await message.channel.send("You are not authorized to use this command.")
            return
        set_sync_enabled(False)
        await message.channel.send("Sync paused.")
        return

    if content == 'a!sync':
        if not is_controlled_user(message.author.id):
            await message.channel.send("You are not authorized to use this command.")
            return
        try:
            await perform_manual_sync(message)
        except ValueError as exc:
            await message.channel.send(str(exc))
        return
    if content == 'a!debug':
        # Provide a quick debug dump to the invoking channel
        config_data = load_config()
        dangerous_perms_data = load_dangerous_perms()
        source_guild_ids = get_source_guild_ids(config_data)
        role_mapping = build_role_mapping(config_data)
        lines = [
            f"Debug dump:",
            f"Bot ID: {bot.user.id}",
            f"Members intent: {bot.intents.members}",
            f"Source guild IDs: {source_guild_ids}",
            f"Target guild ID: {config_data['TARGET_GUILD_ID']}",
            f"Role mappings loaded: {len(role_mapping)} entries",
            f"Dangerous permissions loaded: {len(dangerous_perms_data.get('dangerous_permissions', []))}"
        ]
        # show a few mapping samples
        sample = list(role_mapping.items())[:10]
        for s, t in sample:
            lines.append(f"  {s} -> {t}")
        await message.channel.send("\n".join(lines))
        return

    await bot.process_commands(message)


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