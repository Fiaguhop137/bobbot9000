from __future__ import annotations

import asyncio
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


# Minecraft server is intentionally hardcoded.
MINECRAFT_DIRECTORY = "/home/firebot/Downloads/minecraft_server"
MINECRAFT_JAR = (
    "fabric-server-mc.1.21.1-loader.0.19.3-launcher.1.1.2.jar"
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
RESTART_SCRIPT = os.path.join(BOT_DIRECTORY, "restart.sh")

LOG_FILE = "bot.log"
CHAT_LOG_FILE = "chat.log"


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
    guild = ctx.guild.name if ctx.guild else "DM"
    channel = getattr(ctx.channel, "name", "unknown")
    user = user_tag(ctx.author)
    timestamp = datetime.now(timezone.utc).isoformat()

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
    write_file(LOG_FILE, line)


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
        content += f" ({pending_chat['count']})"

    line = (
        f"[{pending_chat['timestamp']}] "
        f'Guild="{pending_chat["guild"]}" | '
        f'Channel="#{pending_chat["channel"]}" | '
        f'User="{pending_chat["author"]}" '
        f'({pending_chat["author_id"]}) | '
        f'Content="{content}"'
    )

    write_file(CHAT_LOG_FILE, line)

    pending_chat = None


@bot.listen("on_message")
async def log_chat(message: discord.Message):
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
        and pending_chat["author_id"] == message.author.id
        and pending_chat["content"] == message.content
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": 1,
    }


# ============================================================
# Permissions
# ============================================================

def admin_only():
    async def predicate(ctx: commands.Context):
        return (
            ctx.guild is not None
            and ctx.author.guild_permissions.administrator
        )

    return commands.check(predicate)


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


async def mc_command(command: str) -> str:
    # MCRcon uses signal handling internally in this version,
    # so it must remain in the main Python thread.
    return rcon(command)


async def send_mc_result(
    ctx: commands.Context,
    response: str,
):
    response = response or "Command executed successfully."

    await ctx.send(
        f"```text\n{response[:1900]}\n```"
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


def start_minecraft_server() -> subprocess.Popen:
    global minecraft_process

    if minecraft_running():
        raise RuntimeError(
            f"Minecraft server is already running "
            f"(PID {minecraft_process.pid})"
        )

    if not os.path.isdir(MINECRAFT_DIRECTORY):
        raise FileNotFoundError(
            f"Minecraft directory does not exist:\n"
            f"{MINECRAFT_DIRECTORY}"
        )

    jar_path = os.path.join(
        MINECRAFT_DIRECTORY,
        MINECRAFT_JAR,
    )

    if not os.path.isfile(jar_path):
        raise FileNotFoundError(
            f"Minecraft server JAR does not exist:\n"
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
        f"{datetime.now(timezone.utc).isoformat()} =====\n"
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
            response = await mc_command("stop")
            return response or "Stop command sent."
        except Exception:
            return "Minecraft server does not appear to be running."

    try:
        response = await mc_command("stop")

        for _ in range(60):
            if minecraft_process.poll() is not None:
                minecraft_process = None
                return response or "Minecraft server stopped."

            await asyncio.sleep(1)

        raise RuntimeError(
            "Minecraft did not stop within 60 seconds."
        )

    except Exception:
        raise


# ============================================================
# Targeting
# ============================================================

def resolve_targets():
    targets = []

    for guild in bot.guilds:
        if (
            target_server != "all"
            and target_server.lower() not in guild.name.lower()
        ):
            continue

        if guild.me is None:
            continue

        for channel in guild.text_channels:
            permissions = channel.permissions_for(guild.me)

            if not permissions.send_messages:
                continue

            if (
                target_channel == "all"
                or channel.name.lower() == target_channel.lower()
            ):
                targets.append(channel)

    return targets


# ============================================================
# Task Tracking
# ============================================================

def track_task(task: asyncio.Task):
    active_tasks.add(task)

    def finished(completed_task):
        active_tasks.discard(completed_task)

    task.add_done_callback(finished)


# ============================================================
# Minecraft Commands
# ============================================================

@bot.command()
@admin_only()
async def mc(
    ctx: commands.Context,
    *,
    command: str,
):
    await ctx.send(
        "`[Minecraft] Executing command...`"
    )

    try:
        response = await mc_command(command)

        await send_mc_result(
            ctx,
            response,
        )

        log_action(
            ctx,
            f"~mc {command}",
            response or "Command executed successfully.",
        )

    except Exception as e:
        logger.exception(
            "Minecraft RCON command failed"
        )

        await ctx.send(
            f"```text\n"
            f"Minecraft RCON error:\n{e}"
            f"\n```"
        )

        log_action(
            ctx,
            f"~mc {command}",
            str(e),
            success=False,
        )


@bot.command()
@admin_only()
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
            response or "Message sent.",
        )

        log_action(
            ctx,
            f"~mcsay {message}",
            response or "Message sent.",
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
@admin_only()
async def mcstatus(
    ctx: commands.Context,
):
    try:
        response = await mc_command("list")

        await send_mc_result(
            ctx,
            response or "Minecraft server responded.",
        )

    except Exception as e:
        await ctx.send(
            f"```text\n"
            f"Minecraft RCON unavailable:\n{e}"
            f"\n```"
        )


@bot.command()
@admin_only()
async def mcstart(
    ctx: commands.Context,
):
    if minecraft_running():
        await ctx.send(
            f"`Minecraft server is already running "
            f"(PID {minecraft_process.pid}).`"
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
            f"Minecraft server started with PID {process.pid}",
        )

    except Exception as e:
        logger.exception(
            "Failed to start Minecraft server"
        )

        await ctx.send(
            f"```text\n"
            f"Minecraft startup error:\n{e}"
            f"\n```"
        )

        log_action(
            ctx,
            "~mcstart",
            str(e),
            success=False,
        )


@bot.command()
@admin_only()
async def mcstop(
    ctx: commands.Context,
):
    try:
        await ctx.send(
            "`[Minecraft] Stopping server...`"
        )

        response = await stop_minecraft_server()

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
            f"Minecraft shutdown error:\n{e}"
            f"\n```"
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
        f"Latency: {round(bot.latency * 1000)}ms\n"
        f"Connected Guilds: {len(bot.guilds)}\n"
        f"RCON Host: {MINECRAFT_RCON_HOST}\n"
        f"RCON Port: {MINECRAFT_RCON_PORT}\n"
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
                targets = [ctx.channel]

            for target in targets:
                for _ in range(count):
                    await target.send(message)
                    await asyncio.sleep(0.5)

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
        f"Started spam task: `{count}` messages."
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
            await asyncio.sleep(seconds)

            command_text = command.strip()

            if command_text.startswith("~"):
                command_text = command_text[1:]

            fake_message = ctx.message
            original_content = fake_message.content

            fake_message.content = (
                f"~{command_text}"
            )

            try:
                await bot.process_commands(
                    fake_message
                )
            finally:
                fake_message.content = original_content

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
        f"Scheduled command in `{seconds}` seconds."
    )


# ============================================================
# Target Commands
# ============================================================

@bot.command()
@admin_only()
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
            value.removeprefix("#").lower()
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
@admin_only()
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
@admin_only()
async def reboot(
    ctx: commands.Context,
):
    if not os.path.isfile(RESTART_SCRIPT):
        await ctx.send(
            f"`restart.sh not found: {RESTART_SCRIPT}`"
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
        "\n"
        "Minecraft:\n"
        "  ~mc <minecraft command>\n"
        "  ~mcsay <message>\n"
        "  ~mcstatus\n"
        "  ~mcstart\n"
        "  ~mcstop\n"
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
        await ctx.send(
            "`You do not have permission "
            "to use this command.`"
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
        f"Connected to "
        f"{len(bot.guilds)} Discord guild(s)."
    )

    logger.info(
        "Minecraft server: "
        f"{MINECRAFT_DIRECTORY}"
    )

    logger.info(
        "Minecraft RCON: "
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

bot.run(
    DISCORD_TOKEN
)