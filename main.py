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

    logger.info("=" * 50)
    logger.info("⏭️ Skipping startup role sync (disabled)")
    logger.info("=" * 50)

    # Start the periodic sync afterwards
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
                source_member = await source_guild.fetch_member(target_member.id)
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

# --- Manual `a!sync` command and concurrency guard ---
# Track active syncs per target guild to prevent cross-source conflicts
active_sync_targets = set()


async def perform_manual_sync(triggering_message):
    """Perform a one-off sync and update a status message every 2 seconds.
    This version fetches full member lists from source and target guilds to avoid cache misses.
    """
    if not is_sync_enabled():
        if triggering_message:
            await triggering_message.channel.send(
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
        if triggering_message:
            await triggering_message.channel.send(embed=embed)
        else:
            logger.error("Startup sync: one or more configured servers are not available to the bot.")
        return

    if TARGET_GUILD_ID in active_sync_targets:
        embed = discord.Embed(title="Sync Already Running",
                              description="A sync is already running for the target server; cannot start another.",
                              color=discord.Color.orange())
        if triggering_message:
            await triggering_message.channel.send(embed=embed)
        else:
            logger.warning("Startup sync: a sync is already running for the target server; skipping.")
        return

    # Mark sync active for this target
    active_sync_targets.add(TARGET_GUILD_ID)
    embed = discord.Embed(title="Manual Sync Started",
                            description="Fetching members and syncing roles.",
                            color=discord.Color.blue())
    if triggering_message:
        status_msg = await triggering_message.channel.send(embed=embed)
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
            if triggering_message:
                await triggering_message.channel.send(msg)
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
        embed =discord.Embed(title="Manual Sync Complete",
                            description=f"Processed {processed}/{total_members} members - {changes} role changes made",
                            color=discord.Color.green())
        if status_msg:
            await status_msg.edit(embed=embed)
        else:
            logger.info(f"✅ Startup sync complete: Processed {processed}/{total_members} members - {changes} role changes made")

    finally:
        active_sync_targets.discard(TARGET_GUILD_ID)


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
        STAFF_ROLE_ID = 1389925525312507924
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

        CATEGORY_ID = 1389925682678730782

        category = guild.get_channel(CATEGORY_ID)

        safe_name = re.sub(
            r"[^a-z0-9-]",
            "",
            interaction.user.name.lower()
        )

        await guild.create_text_channel(
            name=f"general-{safe_name}",
            category=category,
            overwrites=overwrites
        )      

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
        label="Are you the owner of the group? If not, please provide the owner's username or username ID.",
        style=discord.TextStyle.short,
        required=True
    )
    async def on_submit(self, interaction: discord.Interaction):
        STAFF_ROLE_ID = 1389925525312507924
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

        CATEGORY_ID = 1389925682678730782

        category = guild.get_channel(CATEGORY_ID)

        safe_name = re.sub(
            r"[^a-z0-9-]",
            "",
            interaction.user.name.lower()
        )

        await guild.create_text_channel(
            name=f"partnership-{safe_name}",
            category=category,
            overwrites=overwrites
        )

class InGameReportsModal(discord.ui.Modal, title="In-Game Reports"):
    roblox = discord.ui.TextInput(
        label="What is your Roblox username?",
        required=True,
        min_length=3,
        max_length=32
    )
    reported_user = discord.ui.TextInput(
        label="What is the username of the user you are reporting?",
        style=discord.TextStyle.short,
        required=True
    )
    reason = discord.ui.TextInput(
        label="What is the reason for your report?",
        style=discord.TextStyle.paragraph,
        required=True,
    )
    async def on_submit(self, interaction: discord.Interaction):
            STAFF_ROLE_ID = 1389925525312507924
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

            CATEGORY_ID = 1509601039412625439

            category = guild.get_channel(CATEGORY_ID)
            safe_name = re.sub(
                r"[^a-z0-9-]",
                "",
                interaction.user.name.lower()
            )
            await guild.create_text_channel(
                name=f"report-{safe_name}",
                category=category,
                overwrites=overwrites
            )

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

class SupportTicketView(discord.ui.View):
    @discord.ui.select(custom_id="support_ticket_select")
    async def support_select(self, interaction, select):
        selected_value = select.values[0]
        labels = {
            "general_support": "General Support",
            "partnership_support": "Partnership Support",
            "in_game_reports": "In-Game Reports",
        }
        selected_label = labels.get(selected_value, selected_value)
        if selected_label == "General Support":
            await interaction.response.send_modal(GeneralSupportModal())
            
        elif selected_label == "Partnership Support":
            await interaction.response.send_modal(PartnershipSupportModal())

        elif selected_label == "In-Game Reports":
            await interaction.response.send_modal(InGameReportsModal())

        else: 
            await interaction.response.send_message(
                "```⚠︎⚠︎⚠︎ ERROR. CONTACT RGSRANDOM. ⚠︎⚠︎⚠︎```.",
                ephemeral=True,
            )
            return


@bot.command(name='&^V1mticket')
async def ticket_command(ctx):
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
    await ctx.send(embed=embed, view=SupportTicketView())

@bot.command(name='&^V2ticket')
async def ticket_command(ctx):
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
    await ctx.send(embed=embed, view=SupportTicketView())



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