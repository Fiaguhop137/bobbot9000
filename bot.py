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

LOG_FILE = "bot.log"
CHAT_LOG_FILE = "chat.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)

logger = logging.getLogger("bobbot9000")


# ============================================================
# Discord Configuration
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing."
    )


intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.messages = True
intents.members = True


bot = commands.Bot(
    command_prefix="~",
    intents=intents,
    help_command=None
)


# ============================================================
# Minecraft RCON Configuration
# ============================================================

MINECRAFT_RCON_HOST = os.getenv(
    "MINECRAFT_RCON_HOST",
    "127.0.0.1"
)

MINECRAFT_RCON_PORT = int(
    os.getenv(
        "MINECRAFT_RCON_PORT",
        "25575"
    )
)

MINECRAFT_RCON_PASSWORD = os.getenv(
    "MINECRAFT_RCON_PASSWORD"
)

if not MINECRAFT_RCON_PASSWORD:
    raise RuntimeError(
        "MINECRAFT_RCON_PASSWORD environment variable is missing."
    )


# ============================================================
# Bot Restart Configuration
# ============================================================

BOT_DIRECTORY = os.path.dirname(
    os.path.abspath(__file__)
)

RESTART_SCRIPT = os.path.join(
    BOT_DIRECTORY,
    "restart.sh"
)


# ============================================================
# Global State
# ============================================================

current_target_server = "all"
current_target_channel = "all"

active_tasks: list[asyncio.Task] = []

pending_reboot = False


# ============================================================
# Chat Deduplication
# ============================================================

last_chat_data = {
    "guild": None,
    "channel": None,
    "author": None,
    "author_id": None,
    "content": None,
    "timestamp": None,
    "count": 0,
}


# ============================================================
# Chat Logging
# ============================================================

def flush_chat_log() -> None:

    global last_chat_data

    if last_chat_data["content"] is None:
        return

    content = last_chat_data["content"]

    if last_chat_data["count"] > 1:
        content = (
            f"{content} "
            f"({last_chat_data['count']})"
        )

    line = (
        f"[{last_chat_data['timestamp']}] "
        f'Guild="{last_chat_data["guild"]}" | '
        f'Channel="#{last_chat_data["channel"]}" | '
        f'User="{last_chat_data["author"]}" '
        f'({last_chat_data["author_id"]}) | '
        f'Content="{content}"'
    )

    try:

        with open(
            CHAT_LOG_FILE,
            "a",
            encoding="utf-8"
        ) as file:

            file.write(
                line + "\n"
            )

    except OSError as e:

        logger.error(
            f"Failed to write chat log: {e}"
        )

    last_chat_data["count"] = 0
    last_chat_data["content"] = None


# ============================================================
# Audit Logging
# ============================================================

def log_action(
    *,
    guild: discord.Guild | None,
    channel: discord.abc.Messageable | None,
    user: discord.abc.User,
    command: str,
    action: str,
    success: bool = True,
) -> None:

    guild_name = (
        guild.name
        if guild
        else "DM"
    )

    channel_name = getattr(
        channel,
        "name",
        "unknown"
    )

    username = (
        f"{user.name}#{user.discriminator}"
        if user.discriminator != "0"
        else user.name
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    line = (
        f"[{now}] "
        f"{'SUCCESS' if success else 'ERROR'} | "
        f'Guild="{guild_name}" | '
        f'Channel="#{channel_name}" | '
        f'User="{username}" ({user.id}) | '
        f'Command="{command}" | '
        f'Action="{action}"'
    )

    logger.info(line)

    try:

        with open(
            LOG_FILE,
            "a",
            encoding="utf-8"
        ) as file:

            file.write(
                line + "\n"
            )

    except OSError as e:

        logger.error(
            f"Failed to write audit log: {e}"
        )


# ============================================================
# Permission Check
# ============================================================

def is_admin():

    async def predicate(
        ctx: commands.Context
    ) -> bool:

        if ctx.guild is None:
            return False

        return (
            ctx.author.guild_permissions.administrator
        )

    return commands.check(
        predicate
    )


# ============================================================
# Minecraft RCON
# ============================================================

def minecraft_command(
    command: str
) -> str:

    with MCRcon(
        MINECRAFT_RCON_HOST,
        MINECRAFT_RCON_PASSWORD,
        port=MINECRAFT_RCON_PORT,
    ) as rcon:

        return rcon.command(
            command
        )


async def run_minecraft_command(
    command: str
) -> str:

    return await asyncio.to_thread(
        minecraft_command,
        command
    )


# ============================================================
# Target Resolver
# ============================================================

def resolve_targets() -> list[discord.TextChannel]:

    targets = []

    for guild in bot.guilds:

        if (
            current_target_server != "all"
            and current_target_server.lower()
            not in guild.name.lower()
        ):
            continue

        for channel in guild.text_channels:

            permissions = channel.permissions_for(
                guild.me
            )

            if not permissions.send_messages:
                continue

            if (
                current_target_channel == "all"
                or channel.name.lower()
                == current_target_channel.lower()
            ):

                targets.append(
                    channel
                )

    return targets


# ============================================================
# Execute Delayed Command
# ============================================================

async def execute_delayed_command(
    ctx: commands.Context,
    command_string: str
):

    try:

        message = command_string.strip()

        if not message:
            return

        if message.startswith("~"):
            message = message[1:].strip()

        parts = message.split(
            " ",
            1
        )

        command_name = parts[0]

        command_args = (
            parts[1]
            if len(parts) > 1
            else ""
        )

        command = bot.get_command(
            command_name
        )

        if command is None:

            await ctx.channel.send(
                f"`Unknown delayed command: "
                f"{command_name}`"
            )

            return

        fake_message = ctx.message

        fake_message.content = (
            f"~{command_name}"
            + (
                f" {command_args}"
                if command_args
                else ""
            )
        )

        await bot.process_commands(
            fake_message
        )

    except asyncio.CancelledError:
        return

    except Exception as e:

        logger.exception(
            "[Delay] Delayed command failed"
        )

        await ctx.channel.send(
            f"`Delayed command error: {e}`"
        )


# ============================================================
# Minecraft Command
# ============================================================

@bot.command()
@is_admin()
async def mc(
    ctx: commands.Context,
    *,
    command: str
):

    await ctx.send(
        "`[Minecraft] Executing command...`"
    )

    try:

        response = await run_minecraft_command(
            command
        )

        if not response:
            response = (
                "Command executed successfully."
            )

        response = response[:1900]

        await ctx.send(
            f"```text\n"
            f"{response}"
            f"\n```"
        )

        log_action(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            command=f"~mc {command}",
            action=response,
        )

    except Exception as e:

        logger.exception(
            "[Minecraft] RCON command failed"
        )

        await ctx.send(
            "```text\n"
            f"Minecraft RCON error:\n{e}"
            "\n```"
        )

        log_action(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            command=f"~mc {command}",
            action=str(e),
            success=False,
        )


# ============================================================
# Minecraft Say
# ============================================================

@bot.command()
@is_admin()
async def mcsay(
    ctx: commands.Context,
    *,
    message: str
):

    command = f"say {message}"

    try:

        response = await run_minecraft_command(
            command
        )

        if not response:
            response = "Message sent."

        await ctx.send(
            f"```text\n"
            f"{response[:1900]}"
            f"\n```"
        )

        log_action(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            command=f"~mcsay {message}",
            action=response,
        )

    except Exception as e:

        await ctx.send(
            f"`Minecraft RCON error: {e}`"
        )

        log_action(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            command=f"~mcsay {message}",
            action=str(e),
            success=False,
        )


# ============================================================
# Minecraft Status
# ============================================================

@bot.command()
@is_admin()
async def mcstatus(
    ctx: commands.Context
):

    try:

        response = await run_minecraft_command(
            "list"
        )

        if not response:
            response = (
                "Minecraft server responded."
            )

        await ctx.send(
            f"```text\n"
            f"{response[:1900]}"
            f"\n```"
        )

    except Exception as e:

        await ctx.send(
            "```text\n"
            f"Minecraft RCON unavailable:\n{e}"
            "\n```"
        )


# ============================================================
# Test
# ============================================================

@bot.command()
async def test(
    ctx: commands.Context
):

    hostname = socket.gethostname()

    latency = round(
        bot.latency * 1000
    )

    await ctx.send(
        "```text\n"
        "Diagnostic Report\n"
        "-------------------------\n"
        "Status: Online\n"
        f"Host: {hostname}\n"
        f"Latency: {latency}ms\n"
        f"Connected Guilds: {len(bot.guilds)}\n"
        f"RCON Host: {MINECRAFT_RCON_HOST}\n"
        f"RCON Port: {MINECRAFT_RCON_PORT}\n"
        "-------------------------\n"
        "```"
    )


# ============================================================
# Echo
# ============================================================

@bot.command()
async def echo(
    ctx: commands.Context,
    *,
    message: str
):

    await ctx.send(
        message
    )

    log_action(
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        command=f"~echo {message}",
        action="Discord echo"
    )


# ============================================================
# Spam
# ============================================================

@bot.command()
async def spam(
    ctx: commands.Context,
    count: int,
    *,
    message: str
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

                    await target.send(
                        message
                    )

                    await asyncio.sleep(
                        0.5
                    )

        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(
        spam_task()
    )

    active_tasks.append(
        task
    )

    def remove_task(
        completed_task: asyncio.Task
    ):

        if completed_task in active_tasks:

            active_tasks.remove(
                completed_task
            )

    task.add_done_callback(
        remove_task
    )

    await ctx.send(
        f"Started spam task: "
        f"`{count}` messages."
    )


# ============================================================
# Stop
# ============================================================

@bot.command()
async def stop(
    ctx: commands.Context
):

    count = len(
        active_tasks
    )

    for task in active_tasks:
        task.cancel()

    active_tasks.clear()

    await ctx.send(
        f"Cancelled `{count}` active task(s)."
    )


# ============================================================
# Delay
# ============================================================

@bot.command()
async def delay(
    ctx: commands.Context,
    seconds: int,
    *,
    command: str
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

            await execute_delayed_command(
                ctx,
                command
            )

        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(
        delayed_task()
    )

    active_tasks.append(
        task
    )

    task.add_done_callback(
        lambda t:
        active_tasks.remove(t)
        if t in active_tasks
        else None
    )

    await ctx.send(
        f"Scheduled command in "
        f"`{seconds}` seconds."
    )


# ============================================================
# Set Target
# ============================================================

@bot.command()
@is_admin()
async def settarget(
    ctx: commands.Context,
    target_type: str,
    *,
    value: str
):

    global current_target_server
    global current_target_channel

    target_type = target_type.lower()

    if target_type == "server":

        current_target_server = value

    elif target_type == "channel":

        clean_value = (
            value
            .removeprefix("#")
            .lower()
        )

        current_target_channel = (
            clean_value
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
        f"Target server: "
        f"{current_target_server}\n"
        f"Target channel: "
        f"#{current_target_channel}\n"
        "```"
    )


# ============================================================
# Targets
# ============================================================

@bot.command()
async def targets(
    ctx: commands.Context
):

    await ctx.send(
        "```text\n"
        f"Server: {current_target_server}\n"
        f"Channel: #{current_target_channel}\n"
        "```"
    )


# ============================================================
# Servers
# ============================================================

@bot.command()
@is_admin()
async def servers(
    ctx: commands.Context
):

    output = [
        "Connected Discord Servers",
        "-------------------------"
    ]

    for guild in bot.guilds:

        output.append(
            f"{guild.name} ({guild.id})"
        )

        for channel in guild.text_channels:

            permissions = channel.permissions_for(
                guild.me
            )

            if permissions.send_messages:

                output.append(
                    f"  #{channel.name}"
                )

    text = "\n".join(
        output
    )

    await ctx.send(
        f"```text\n"
        f"{text[:1900]}"
        f"\n```"
    )


# ============================================================
# Reboot
# ============================================================

@bot.command()
@is_admin()
async def reboot(
    ctx: commands.Context
):

    global pending_reboot

    await ctx.send(
        "`[System] Reboot flag set. "
        "Restarting bot...`"
    )

    log_action(
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        command="~reboot",
        action="Bot restart requested"
    )

    pending_reboot = True


# ============================================================
# Reboot Watcher
# ============================================================

async def reboot_watcher():

    global pending_reboot

    await bot.wait_until_ready()

    while not bot.is_closed():

        if pending_reboot:

            logger.info(
                "[System] Reboot command detected."
            )

            flush_chat_log()

            if not os.path.isfile(
                RESTART_SCRIPT
            ):

                logger.error(
                    "[System] restart.sh not found: "
                    f"{RESTART_SCRIPT}"
                )

                pending_reboot = False

                await asyncio.sleep(
                    2
                )

                continue

            try:

                subprocess.Popen(
                    [RESTART_SCRIPT],
                    cwd=BOT_DIRECTORY,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )

                logger.info(
                    "[System] restart.sh launched."
                )

            except Exception:

                logger.exception(
                    "[System] Failed to launch restart.sh"
                )

                pending_reboot = False

                await asyncio.sleep(
                    2
                )

                continue

            await asyncio.sleep(
                1
            )

            await bot.close()

            return

        await asyncio.sleep(
            0.1
        )


# ============================================================
# Help
# ============================================================

@bot.command(name="help")
async def help_command(
    ctx: commands.Context
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
# Unknown Command Handler
# ============================================================

@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):

        return

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(
            f"`Missing argument: "
            f"{error.param.name}`"
        )

        return

    if isinstance(
        error,
        commands.BadArgument
    ):

        await ctx.send(
            "`Invalid argument.`"
        )

        return

    if isinstance(
        error,
        commands.CheckFailure
    ):

        await ctx.send(
            "`You do not have permission "
            "to use this command.`"
        )

        return

    logger.exception(
        "Unhandled command error",
        exc_info=error
    )

    await ctx.send(
        "`An unexpected error occurred.`"
    )


# ============================================================
# Discord Message Handler
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    global last_chat_data

    if message.author == bot.user:
        return

    # --------------------------------------------------------
    # Chat logging
    # --------------------------------------------------------

    if message.guild:

        guild_name = (
            message.guild.name
        )

        channel_name = (
            message.channel.name
        )

        author_tag = (
            f"{message.author.name}"
            f"#{message.author.discriminator}"
            if message.author.discriminator != "0"
            else message.author.name
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        if (
            last_chat_data["guild"]
            == guild_name
            and last_chat_data["channel"]
            == channel_name
            and last_chat_data["author_id"]
            == message.author.id
            and last_chat_data["content"]
            == message.content
        ):

            last_chat_data["count"] += 1

        else:

            flush_chat_log()

            last_chat_data = {
                "guild": guild_name,
                "channel": channel_name,
                "author": author_tag,
                "author_id": message.author.id,
                "content": message.content,
                "timestamp": now,
                "count": 1,
            }

    # --------------------------------------------------------
    # Command processing
    # --------------------------------------------------------

    await bot.process_commands(
        message
    )


# ============================================================
# Bot Ready
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
        "Minecraft RCON: "
        f"{MINECRAFT_RCON_HOST}:"
        f"{MINECRAFT_RCON_PORT}"
    )

    logger.info(
        "Command prefix: ~"
    )

    # Prevent multiple watcher tasks if Discord
    # reconnects and on_ready fires again.
    if not hasattr(
        bot,
        "_reboot_watcher_started"
    ):

        bot._reboot_watcher_started = True

        bot.loop.create_task(
            reboot_watcher()
        )


# ============================================================
# Shutdown
# ============================================================

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