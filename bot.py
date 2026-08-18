from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv
from mcrcon import MCRcon


# ============================================================
# Configuration
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MINECRAFT_RCON_PASSWORD = os.getenv("MINECRAFT_RCON_PASSWORD")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from .env")

if not MINECRAFT_RCON_PASSWORD:
    raise RuntimeError("MINECRAFT_RCON_PASSWORD is missing from .env")


# ============================================================
# Hardcoded Configuration
# ============================================================

OWNER_ID = 789314712990384168

MINECRAFT_DIRECTORY = "/home/firebot/git/bobbot9000/minecraft_server"

MINECRAFT_JAR = (
    "fabric-server-mc.1.21.11-loader.0.19.3-launcher.1.1.2.jar"
)

MINECRAFT_RCON_HOST = "127.0.0.1"
MINECRAFT_RCON_PORT = 25575

MINECRAFT_COMMAND = [
    "java",
    "-Xmx3G",
    "-Xms3G",
    "-jar",
    MINECRAFT_JAR,
    "nogui",
]

BOT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))

PERMISSIONS_FILE = os.path.join(
    BOT_DIRECTORY,
    "permissions.json",
)

RESTART_SCRIPT = os.path.join(
    BOT_DIRECTORY,
    "restart.sh",
)

LOG_FILE = os.path.join(
    BOT_DIRECTORY,
    "bot.log",
)

CHAT_LOG_FILE = os.path.join(
    BOT_DIRECTORY,
    "chat.log",
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

logger = logging.getLogger("bobbot9000")


def write_file(path: str, line: str):
    try:
        with open(path, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError as e:
        logger.error(f"Failed to write {path}: {e}")


# ============================================================
# Permissions
# ============================================================

def default_permissions():
    return {
        "owner": str(OWNER_ID),
        "admins": [],
        "mods": [],
    }


def save_permissions():
    try:
        temp_file = PERMISSIONS_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                permissions,
                file,
                indent=4,
            )
            file.write("\n")

        os.replace(
            temp_file,
            PERMISSIONS_FILE,
        )

    except OSError as e:
        logger.error(
            f"Failed to save permissions: {e}"
        )


def load_permissions():
    global permissions

    if not os.path.isfile(PERMISSIONS_FILE):
        logger.info(
            "[System] permissions.json not found."
        )

        permissions = default_permissions()

        save_permissions()

        logger.info(
            "[System] Created permissions.json."
        )

        return

    try:
        with open(
            PERMISSIONS_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError(
                "permissions.json must contain an object."
            )

        permissions = data

    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as e:
        logger.error(
            f"Failed to load permissions.json: {e}"
        )

        logger.info(
            "[System] Recreating permissions.json."
        )

        permissions = default_permissions()
        save_permissions()

    # Make sure required fields exist.
    permissions.setdefault(
        "owner",
        str(OWNER_ID),
    )

    permissions.setdefault(
        "admins",
        [],
    )

    permissions.setdefault(
        "mods",
        [],
    )

    # The hardcoded owner always wins.
    if permissions["owner"] != str(OWNER_ID):
        logger.warning(
            "[Permissions] Owner ID in JSON did not match "
            "the hardcoded owner. Restoring owner."
        )

        permissions["owner"] = str(OWNER_ID)
        save_permissions()


permissions = {}
load_permissions()


def get_role(user_id: int) -> str | None:
    user_id = str(user_id)

    # Owner is always the hardcoded owner.
    if user_id == str(OWNER_ID):
        return "owner"

    if user_id in permissions.get("admins", []):
        return "admin"

    if user_id in permissions.get("mods", []):
        return "mod"

    return None


def role_level(role: str | None) -> int:
    levels = {
        None: 0,
        "mod": 1,
        "admin": 2,
        "owner": 3,
    }

    return levels.get(role, 0)


def has_role(
    user_id: int,
    minimum_role: str,
) -> bool:
    user_role = get_role(user_id)

    return (
        role_level(user_role)
        >= role_level(minimum_role)
    )


def bot_permission(minimum_role: str):
    async def predicate(ctx: commands.Context):
        return has_role(
            ctx.author.id,
            minimum_role,
        )

    return commands.check(predicate)


# ============================================================
# User Helpers
# ============================================================

def user_tag(user: discord.abc.User) -> str:
    if user.discriminator != "0":
        return f"{user.name}#{user.discriminator}"

    return user.name


def log_action(
    ctx: commands.Context,
    command: str,
    action: str,
    success: bool = True,
):
    guild = (
        ctx.guild.name
        if ctx.guild
        else "DM"
    )

    channel = getattr(
        ctx.channel,
        "name",
        "unknown",
    )

    user = user_tag(ctx.author)

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    line = (
        f"[{timestamp}] "
        f"{'SUCCESS' if success else 'ERROR'} | "
        f'Guild="{guild}" | '
        f'Channel="#{channel}" | '
        f'User="{user}" ({ctx.author.id}) | '
        f'Command="{command}" | '
        f'Action="{action}"'
    )

    logger.info(line)
    write_file(
        LOG_FILE,
        line,
    )


# ============================================================
# Discord
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.guild_messages = True
intents.messages = True
intents.members = True

bot = commands.Bot(
    command_prefix="~",
    intents=intents,
    help_command=None,
)


# ============================================================
# Global State
# ============================================================

target_server = "all"
target_channel = "all"

active_tasks: set[asyncio.Task] = set()

pending_chat = None

minecraft_process: subprocess.Popen | None = None


# ============================================================
# Chat Logging
# ============================================================

def flush_chat_log():
    global pending_chat

    if not pending_chat:
        return

    content = pending_chat["content"]

    if pending_chat["count"] > 1:
        content += (
            f" ({pending_chat['count']})"
        )

    line = (
        f"[{pending_chat['timestamp']}] "
        f'Guild="{pending_chat["guild"]}" | '
        f'Channel="#{pending_chat["channel"]}" | '
        f'User="{pending_chat["author"]}" '
        f'({pending_chat["author_id"]}) | '
        f'Content="{content}"'
    )

    write_file(
        CHAT_LOG_FILE,
        line,
    )

    pending_chat = None


@bot.listen("on_message")
async def log_chat(
    message: discord.Message,
):
    global pending_chat

    if message.author == bot.user:
        return

    if not message.guild:
        return

    guild = message.guild.name
    channel = message.channel.name
    author = user_tag(message.author)

    same_message = (
        pending_chat
        and pending_chat["guild"] == guild
        and pending_chat["channel"] == channel
        and pending_chat["author_id"]
        == message.author.id
        and pending_chat["content"]
        == message.content
    )

    if same_message:
        pending_chat["count"] += 1
        return

    flush_chat_log()

    pending_chat = {
        "guild": guild,
        "channel": channel,
        "author": author,
        "author_id": message.author.id,
        "content": message.content,
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "count": 1,
    }


# ============================================================
# Minecraft RCON
# ============================================================

def rcon(command: str) -> str:
    with MCRcon(
        MINECRAFT_RCON_HOST,
        MINECRAFT_RCON_PASSWORD,
        port=MINECRAFT_RCON_PORT,
    ) as connection:
        return connection.command(command)


async def mc_command(
    command: str,
) -> str:
    # mcrcon in the installed version uses signal handling,
    # so do NOT put this into asyncio.to_thread().
    return rcon(command)


async def send_mc_result(
    ctx: commands.Context,
    response: str,
):
    response = (
        response
        or "Command executed successfully."
    )

    await ctx.send(
        f"```text\n"
        f"{response[:1900]}"
        f"\n```"
    )


# ============================================================
# Minecraft Server Process
# ============================================================

def minecraft_running() -> bool:
    global minecraft_process

    if minecraft_process is None:
        return False

    if minecraft_process.poll() is None:
        return True

    minecraft_process = None

    return False


def start_minecraft_server():
    global minecraft_process

    if minecraft_running():
        raise RuntimeError(
            "Minecraft server is already running "
            f"(PID {minecraft_process.pid})"
        )

    if not os.path.isdir(
        MINECRAFT_DIRECTORY
    ):
        raise FileNotFoundError(
            "Minecraft directory does not exist:\n"
            f"{MINECRAFT_DIRECTORY}"
        )

    jar_path = os.path.join(
        MINECRAFT_DIRECTORY,
        MINECRAFT_JAR,
    )

    if not os.path.isfile(jar_path):
        raise FileNotFoundError(
            "Minecraft server JAR does not exist:\n"
            f"{jar_path}"
        )

    log_path = os.path.join(
        MINECRAFT_DIRECTORY,
        "server-console.log",
    )

    log_file = open(
        log_path,
        "a",
        encoding="utf-8",
    )

    log_file.write(
        "\n\n"
        f"===== Server started "
        f"{datetime.now(timezone.utc).isoformat()} "
        "=====\n"
    )

    log_file.flush()

    minecraft_process = subprocess.Popen(
        MINECRAFT_COMMAND,
        cwd=MINECRAFT_DIRECTORY,
        stdin=subprocess.PIPE,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    return minecraft_process


async def stop_minecraft_server():
    global minecraft_process

    if not minecraft_running():
        minecraft_process = None

        try:
            response = await mc_command(
                "stop"
            )

            return (
                response
                or "Stop command sent."
            )

        except Exception:
            return (
                "Minecraft server does not "
                "appear to be running."
            )

    response = await mc_command(
        "stop"
    )

    for _ in range(60):
        if minecraft_process is None:
            return (
                response
                or "Minecraft server stopped."
            )

        if minecraft_process.poll() is not None:
            minecraft_process = None

            return (
                response
                or "Minecraft server stopped."
            )

        await asyncio.sleep(1)

    raise RuntimeError(
        "Minecraft did not stop within 60 seconds."
    )


# ============================================================
# Targeting
# ============================================================

def resolve_targets():
    targets = []

    for guild in bot.guilds:

        if (
            target_server != "all"
            and target_server.lower()
            not in guild.name.lower()
        ):
            continue

        if guild.me is None:
            continue

        for channel in guild.text_channels:

            permissions_for_bot = (
                channel.permissions_for(
                    guild.me
                )
            )

            if not permissions_for_bot.send_messages:
                continue

            if (
                target_channel == "all"
                or channel.name.lower()
                == target_channel.lower()
            ):
                targets.append(channel)

    return targets


# ============================================================
# Task Tracking
# ============================================================

def track_task(task: asyncio.Task):
    active_tasks.add(task)

    def finished(completed_task):
        active_tasks.discard(
            completed_task
        )

    task.add_done_callback(
        finished
    )


# ============================================================
# Permission Commands
# ============================================================

@bot.command()
@bot_permission("owner")
async def grant(
    ctx: commands.Context,
    role: str,
    user_id: int,
):
    role = role.lower()

    if role not in {
        "admin",
        "mod",
    }:
        await ctx.send(
            "Usage: `~grant <admin|mod> <user_id>`"
        )
        return

    user_id_string = str(user_id)

    # Prevent the owner from being accidentally placed
    # into another role.
    if user_id == OWNER_ID:
        await ctx.send(
            "`The owner already has every permission.`"
        )
        return

    # Remove the user from every role first.
    permissions["admins"] = [
        uid
        for uid in permissions["admins"]
        if uid != user_id_string
    ]

    permissions["mods"] = [
        uid
        for uid in permissions["mods"]
        if uid != user_id_string
    ]

    permissions[role + "s"].append(
        user_id_string
    )

    save_permissions()

    await ctx.send(
        f"`Granted {role} permissions to "
        f"{user_id}.`"
    )

    log_action(
        ctx,
        f"~grant {role} {user_id}",
        f"Granted {role} role to {user_id}",
    )


@bot.command()
@bot_permission("owner")
async def revoke(
    ctx: commands.Context,
    user_id: int,
):
    if user_id == OWNER_ID:
        await ctx.send(
            "`The owner cannot be revoked.`"
        )
        return

    user_id_string = str(user_id)

    found = False

    for role in (
        "admins",
        "mods",
    ):
        if user_id_string in permissions[role]:
            permissions[role].remove(
                user_id_string
            )
            found = True

    if not found:
        await ctx.send(
            "`That user does not have a bot role.`"
        )
        return

    save_permissions()

    await ctx.send(
        f"`Revoked bot permissions from "
        f"{user_id}.`"
    )

    log_action(
        ctx,
        f"~revoke {user_id}",
        f"Revoked permissions from {user_id}",
    )


@bot.command()
@bot_permission("owner")
async def perms(
    ctx: commands.Context,
):
    admins = permissions.get(
        "admins",
        [],
    )

    mods = permissions.get(
        "mods",
        [],
    )

    lines = [
        "Bobbot Permissions",
        "=========================",
        "",
        f"Owner: {OWNER_ID}",
        "",
        "Admins:",
    ]

    if admins:
        lines.extend(
            f"  {user_id}"
            for user_id in admins
        )
    else:
        lines.append(
            "  None"
        )

    lines.append("")
    lines.append("Mods:")

    if mods:
        lines.extend(
            f"  {user_id}"
            for user_id in mods
        )
    else:
        lines.append(
            "  None"
        )

    await ctx.send(
        f"```text\n"
        f"{chr(10).join(lines)}"
        f"\n```"
    )


@bot.command()
async def role(
    ctx: commands.Context,
):
    user_role = get_role(
        ctx.author.id
    )

    if user_role is None:
        user_role = "none"

    await ctx.send(
        f"`Your Bobbot role: {user_role}`"
    )


# ============================================================
# Minecraft Commands
# ============================================================

@bot.command()
@bot_permission("mod")
async def mc(
    ctx: commands.Context,
    *,
    command: str,
):
    await ctx.send(
        "`[Minecraft] Executing command...`"
    )

    try:
        response = await mc_command(
            command
        )

        await send_mc_result(
            ctx,
            response,
        )

        log_action(
            ctx,
            f"~mc {command}",
            response
            or "Command executed successfully.",
        )

    except Exception as e:
        logger.exception(
            "Minecraft RCON command failed"
        )

        await ctx.send(
            f"```text\n"
            f"Minecraft RCON error:\n"
            f"{e}\n"
            f"```"
        )

        log_action(
            ctx,
            f"~mc {command}",
            str(e),
            success=False,
        )


@bot.command()
@bot_permission("mod")
async def mcsay(
    ctx: commands.Context,
    *,
    message: str,
):
    try:
        response = await mc_command(
            f"say {message}"
        )

        await send_mc_result(
            ctx,
            response
            or "Message sent.",
        )

        log_action(
            ctx,
            f"~mcsay {message}",
            response
            or "Message sent.",
        )

    except Exception as e:
        await ctx.send(
            f"`Minecraft RCON error: {e}`"
        )

        log_action(
            ctx,
            f"~mcsay {message}",
            str(e),
            success=False,
        )


@bot.command()
@bot_permission("mod")
async def mcstatus(
    ctx: commands.Context,
):
    try:
        response = await mc_command(
            "list"
        )

        await send_mc_result(
            ctx,
            response
            or "Minecraft server responded.",
        )

    except Exception as e:
        await ctx.send(
            f"```text\n"
            f"Minecraft RCON unavailable:\n"
            f"{e}\n"
            f"```"
        )


@bot.command()
@bot_permission("admin")
async def mcstart(
    ctx: commands.Context,
):
    if minecraft_running():
        await ctx.send(
            f"`Minecraft server is already "
            f"running (PID "
            f"{minecraft_process.pid}).`"
        )
        return

    try:
        process = start_minecraft_server()

        await ctx.send(
            f"`[Minecraft] Server starting. "
            f"PID: {process.pid}`"
        )

        log_action(
            ctx,
            "~mcstart",
            f"Minecraft server started with PID "
            f"{process.pid}",
        )

    except Exception as e:
        logger.exception(
            "Failed to start Minecraft server"
        )

        await ctx.send(
            f"```text\n"
            f"Minecraft startup error:\n"
            f"{e}\n"
            f"```"
        )

        log_action(
            ctx,
            "~mcstart",
            str(e),
            success=False,
        )


@bot.command()
@bot_permission("admin")
async def mcstop(
    ctx: commands.Context,
):
    try:
        await ctx.send(
            "`[Minecraft] Stopping server...`"
        )

        response = (
            await stop_minecraft_server()
        )

        await ctx.send(
            f"`{response}`"
        )

        log_action(
            ctx,
            "~mcstop",
            response,
        )

    except Exception as e:
        logger.exception(
            "Failed to stop Minecraft server"
        )

        await ctx.send(
            f"```text\n"
            f"Minecraft shutdown error:\n"
            f"{e}\n"
            f"```"
        )

        log_action(
            ctx,
            "~mcstop",
            str(e),
            success=False,
        )


# ============================================================
# General Commands
# ============================================================

@bot.command()
async def test(
    ctx: commands.Context,
):
    await ctx.send(
        "```text\n"
        "Diagnostic Report\n"
        "-------------------------\n"
        "Status: Online\n"
        f"Host: {socket.gethostname()}\n"
        f"Latency: "
        f"{round(bot.latency * 1000)}ms\n"
        f"Connected Guilds: "
        f"{len(bot.guilds)}\n"
        f"RCON Host: "
        f"{MINECRAFT_RCON_HOST}\n"
        f"RCON Port: "
        f"{MINECRAFT_RCON_PORT}\n"
        f"Minecraft Process: "
        f"{'Running' if minecraft_running() else 'Not tracked'}\n"
        "-------------------------\n"
        "```"
    )


@bot.command()
async def echo(
    ctx: commands.Context,
    *,
    message: str,
):
    await ctx.send(message)

    log_action(
        ctx,
        f"~echo {message}",
        "Discord echo",
    )


@bot.command()
async def spam(
    ctx: commands.Context,
    count: int,
    *,
    message: str,
):
    if count < 1:
        await ctx.send(
            "Count must be at least 1."
        )
        return

    if count > 100:
        await ctx.send(
            "Maximum spam count is 100."
        )
        return

    async def spam_task():
        try:
            targets = resolve_targets()

            if not targets:
                targets = [
                    ctx.channel
                ]

            for target in targets:
                for _ in range(count):
                    await target.send(
                        message
                    )

                    await asyncio.sleep(
                        0.5
                    )

        except asyncio.CancelledError:
            pass

        except Exception:
            logger.exception(
                "Spam task failed"
            )

    task = asyncio.create_task(
        spam_task()
    )

    track_task(task)

    await ctx.send(
        f"Started spam task: "
        f"`{count}` messages."
    )


@bot.command()
async def stop(
    ctx: commands.Context,
):
    count = len(active_tasks)

    for task in list(active_tasks):
        task.cancel()

    active_tasks.clear()

    await ctx.send(
        f"Cancelled `{count}` active task(s)."
    )


@bot.command()
async def delay(
    ctx: commands.Context,
    seconds: int,
    *,
    command: str,
):
    if seconds < 0:
        await ctx.send(
            "Delay cannot be negative."
        )
        return

    async def delayed_task():
        try:
            await asyncio.sleep(
                seconds
            )

            command_text = command.strip()

            if command_text.startswith("~"):
                command_text = (
                    command_text[1:]
                )

            fake_message = ctx.message

            original_content = (
                fake_message.content
            )

            fake_message.content = (
                f"~{command_text}"
            )

            try:
                await bot.process_commands(
                    fake_message
                )

            finally:
                fake_message.content = (
                    original_content
                )

        except asyncio.CancelledError:
            pass

        except Exception:
            logger.exception(
                "Delayed command failed"
            )

    task = asyncio.create_task(
        delayed_task()
    )

    track_task(task)

    await ctx.send(
        f"Scheduled command in "
        f"`{seconds}` seconds."
    )


# ============================================================
# Target Commands
# ============================================================

@bot.command()
@bot_permission("admin")
async def settarget(
    ctx: commands.Context,
    target_type: str,
    *,
    value: str,
):
    global target_server
    global target_channel

    target_type = target_type.lower()

    if target_type == "server":
        target_server = value

    elif target_type == "channel":
        target_channel = (
            value.removeprefix(
                "#"
            ).lower()
        )

    else:
        await ctx.send(
            "Usage:\n"
            "`~settarget server <name|all>`\n"
            "`~settarget channel <name|all>`"
        )
        return

    await ctx.send(
        "```text\n"
        f"Target server: {target_server}\n"
        f"Target channel: #{target_channel}\n"
        "```"
    )


@bot.command()
async def targets(
    ctx: commands.Context,
):
    await ctx.send(
        "```text\n"
        f"Server: {target_server}\n"
        f"Channel: #{target_channel}\n"
        "```"
    )


@bot.command()
@bot_permission("admin")
async def servers(
    ctx: commands.Context,
):
    lines = [
        "Connected Discord Servers",
        "-------------------------",
    ]

    for guild in bot.guilds:

        lines.append(
            f"{guild.name} ({guild.id})"
        )

        if guild.me is None:
            continue

        for channel in guild.text_channels:

            if channel.permissions_for(
                guild.me
            ).send_messages:

                lines.append(
                    f"  #{channel.name}"
                )

    await ctx.send(
        f"```text\n"
        f"{chr(10).join(lines)[:1900]}"
        f"\n```"
    )


# ============================================================
# Reboot
# ============================================================

@bot.command()
@bot_permission("admin")
async def reboot(
    ctx: commands.Context,
):
    if not os.path.isfile(
        RESTART_SCRIPT
    ):
        await ctx.send(
            f"`restart.sh not found: "
            f"{RESTART_SCRIPT}`"
        )
        return

    await ctx.send(
        "`[System] Restarting bot...`"
    )

    log_action(
        ctx,
        "~reboot",
        "Bot restart requested",
    )

    try:
        subprocess.Popen(
            [RESTART_SCRIPT],
            cwd=BOT_DIRECTORY,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        await asyncio.sleep(1)

        flush_chat_log()

        await bot.close()

    except Exception as e:
        logger.exception(
            "Failed to launch restart.sh"
        )

        await ctx.send(
            f"`Restart failed: {e}`"
        )


# ============================================================
# Help
# ============================================================

@bot.command(name="help")
async def help_command(
    ctx: commands.Context,
):
    await ctx.send(
        "```text\n"
        "Bobbot Commands\n"
        "=========================\n"
        "\n"
        "General:\n"
        "  ~test\n"
        "  ~echo <message>\n"
        "  ~spam <count> <message>\n"
        "  ~stop\n"
        "  ~delay <seconds> <command>\n"
        "  ~role\n"
        "\n"
        "Minecraft:\n"
        "  ~mc <minecraft command>\n"
        "  ~mcsay <message>\n"
        "  ~mcstatus\n"
        "  ~mcstart\n"
        "  ~mcstop\n"
        "\n"
        "Permissions:\n"
        "  ~grant <admin|mod> <user_id>\n"
        "  ~revoke <user_id>\n"
        "  ~perms\n"
        "\n"
        "Targeting:\n"
        "  ~settarget server <name|all>\n"
        "  ~settarget channel <name|all>\n"
        "  ~targets\n"
        "  ~servers\n"
        "\n"
        "System:\n"
        "  ~reboot\n"
        "  ~help\n"
        "\n"
        "Permission hierarchy:\n"
        "  Owner = Everything\n"
        "  Admin = Everything except grant/revoke\n"
        "  Mod = Everything except grant/revoke,\n"
        "        mcstart, and mcstop\n"
        "```"
    )


# ============================================================
# Command Errors
# ============================================================

@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
):
    if isinstance(
        error,
        commands.CommandNotFound,
    ):
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument,
    ):
        await ctx.send(
            f"`Missing argument: "
            f"{error.param.name}`"
        )
        return

    if isinstance(
        error,
        commands.BadArgument,
    ):
        await ctx.send(
            "`Invalid argument.`"
        )
        return

    if isinstance(
        error,
        commands.CheckFailure,
    ):
        user_role = get_role(
            ctx.author.id
        )

        if user_role is None:
            user_role = "none"

        await ctx.send(
            f"`You do not have permission "
            f"to use this command. "
            f"Your bot role: {user_role}`"
        )
        return

    logger.exception(
        "Unhandled command error",
        exc_info=error,
    )

    await ctx.send(
        "`An unexpected error occurred.`"
    )


# ============================================================
# Ready / Disconnect
# ============================================================

@bot.event
async def on_ready():
    logger.info(
        f"Logged in as {bot.user} "
        f"(ID: {bot.user.id})"
    )

    logger.info(
        f"Connected to {len(bot.guilds)} "
        f"Discord guild(s)."
    )

    logger.info(
        f"Permission owner: {OWNER_ID}"
    )

    logger.info(
        f"Minecraft server: "
        f"{MINECRAFT_DIRECTORY}"
    )

    logger.info(
        f"Minecraft RCON: "
        f"{MINECRAFT_RCON_HOST}:"
        f"{MINECRAFT_RCON_PORT}"
    )

    logger.info(
        "Command prefix: ~"
    )


@bot.event
async def on_disconnect():
    flush_chat_log()


# ============================================================
# Startup
# ============================================================

logger.info(
    "[System] Starting Bobbot9000..."
)

logger.info(
    f"[Permissions] Owner: {OWNER_ID}"
)

logger.info(
    f"[Permissions] Admins: "
    f"{len(permissions['admins'])}"
)

logger.info(
    f"[Permissions] Mods: "
    f"{len(permissions['mods'])}"
)

bot.run(
    DISCORD_TOKEN
)
