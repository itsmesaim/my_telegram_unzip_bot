# bot.py - PREMIUM UNZIP BOT by @hellopeter3
import asyncio
import json
import logging
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime
from collections import deque
from typing import Dict, List

from dotenv import load_dotenv
from telethon import Button, TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo, InputMediaUploadedDocument

from FastTelethonhelper import fast_download, fast_upload

load_dotenv()

# ============================= LOGS =============================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/bot_{datetime.now().strftime('%Y-%m-%d')}.log", encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("PremiumBot")
logger.info("════════════════════════════════════")
logger.info(" PREMIUM UNZIP BOT STARTED - @hellopeter3")
logger.info("════════════════════════════════════")


class c:
    P = "\033[95m"
    C = "\033[96m"
    G = "\033[92m"
    Y = "\033[93m"
    R = "\033[91m"
    E = "\033[0m"


# ============================= CONFIG =============================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEVELOPER = "hellopeter3"
start_time = datetime.now()

client = TelegramClient(
    StringSession(os.getenv("SESSION_STRING", "")), API_ID, API_HASH
)

# ============================= GLOBALS =============================
user_passwords: Dict[int, bytes] = {}
pending_tasks: Dict[int, bool] = {}
active_uploads: Dict[int, int] = {}

# ============================= SIMPLE QUEUE SYSTEM =============================
# Per-user queues
user_queues: Dict[int, deque] = {}

# Track active tasks per user
user_active_tasks: Dict[int, asyncio.Task] = {}


# ============================= MUTED VIDEO FIX =============================
def video_has_audio(path: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return len(json.loads(result.stdout).get("streams", [])) > 0
    except Exception:
        return True


def add_silent_audio(path: str, muted_list: List[str]) -> None:
    name = os.path.basename(path)
    if video_has_audio(path):
        logger.info(f"{c.G}Audio preserved → {name}{c.E}")
        return
    logger.info(f"{c.Y}Silent video detected → Added silent track: {name}{c.E}")
    muted_list.append(name)  # Collect for final summary
    temp = path + ".silent_fixed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            path,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            temp,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.replace(temp, path)


# ============================= EXTRACT ARCHIVE =============================
def extract_archive(
    file_path: str, extract_to: str, password: bytes | None = None
) -> str:
    try:
        import py7zr, rarfile, tarfile, zipfile
        from pyzipper import AESZipFile

        ext = os.path.splitext(file_path)[1].lower()
        logger.info(
            f"Extracting {os.path.basename(file_path)} → {ext} (pwd: {'Yes' if password else 'No'})"
        )

        if ext == ".zip":
            with AESZipFile(file_path) if password else zipfile.ZipFile(file_path) as z:
                z.extractall(extract_to, pwd=password)
        elif ext == ".7z":
            with py7zr.SevenZipFile(
                file_path, password=password.decode() if password else None
            ) as z:
                z.extractall(extract_to)
        elif ext == ".rar":
            with rarfile.RarFile(file_path) as r:
                r.extractall(extract_to, pwd=password.decode() if password else None)
        elif ext in {".tar", ".gz", ".tgz", ".bz2"}:
            with tarfile.open(file_path) as t:
                t.extractall(extract_to)
        else:
            return "unsupported"
        logger.info("Extraction successful")
        return "success"
    except Exception as e:
        msg = str(e).lower()
        logger.error(f"Extraction failed: {e}")
        if "password" in msg or "wrong" in msg:
            return "password_required"
        return str(e)


# ============================= PROGRESS =============================
async def update_progress(
    msg, cur: int, total: int, action: str = "Processing", zip_name: str = ""
) -> None:
    try:
        bar = "█" * int(20 * cur / total) + "░" * (20 - int(20 * cur / total))
        perc = cur / total * 100
        title = f"{action} ({zip_name})" if zip_name else action
        await msg.edit(
            f"**{title}**\n\n`{bar}` {perc:.1f}%\n{cur:,} / {total:,} files",
            buttons=[[Button.inline("❌ Cancel", b"cancel")]],
        )
    except Exception:
        pass


# ============================= PROCESS ARCHIVE =============================
async def process_archive(task_data):
    """Process a single ZIP for a user"""
    event = task_data["event"]
    status = task_data["status"]
    user_id = task_data["user_id"]
    zip_name = task_data["zip_name"]

    pending_tasks[user_id] = False
    active_uploads[user_id] = 0

    muted_videos = []  # Collect muted fixes for final message

    try:
        # Download
        await status.edit(
            "⬇️ **Downloading...**", buttons=[[Button.inline("❌ Cancel", b"cancel")]]
        )
        path = f"downloads/{zip_name}"
        os.makedirs("downloads", exist_ok=True)

        await fast_download(
            client,
            event.message,
            path,
            progress_bar_function=lambda c, t: asyncio.create_task(
                update_progress(status, c, t, "Downloading", zip_name)
            ),
        )

        # Extract
        extract_to = path + "_extracted"
        os.makedirs(extract_to, exist_ok=True)
        await status.edit(
            "🔓 **Extracting...**", buttons=[[Button.inline("❌ Cancel", b"cancel")]]
        )

        result = extract_archive(path, extract_to, user_passwords.get(user_id))

        if result == "password_required":
            await status.edit("🔒 **Password required!**\nSend `/pass your_password`")
            user_passwords.pop(user_id, None)
            return
        if result != "success":
            await status.edit(f"❌ Failed: {result}")
            shutil.rmtree(extract_to, ignore_errors=True)
            os.remove(path)
            return

        # Scan files with mime fix
        files = []
        for root, _, filenames in os.walk(extract_to):
            for filename in filenames:
                fp = os.path.join(root, filename)

                if os.path.getsize(fp) > 2_000_000_000:
                    logger.info(f"Skipping {filename} (>2GB)")
                    continue

                guessed_mime = mimetypes.guess_type(fp)[0]
                lower_name = filename.lower()

                if guessed_mime is None or guessed_mime == "application/octet-stream":
                    if lower_name.endswith(".pdf"):
                        mime = "application/pdf"
                    elif lower_name.endswith((".doc", ".docx")):
                        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif lower_name.endswith((".xls", ".xlsx")):
                        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    elif lower_name.endswith((".ppt", ".pptx")):
                        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    elif lower_name.endswith((".txt", ".log", ".csv", ".md")):
                        mime = "text/plain"
                    elif lower_name.endswith((".html", ".htm")):
                        mime = "text/html"
                    elif lower_name.endswith(".epub"):
                        mime = "application/epub+zip"
                    else:
                        mime = "application/octet-stream"
                else:
                    mime = guessed_mime

                files.append({"path": fp, "name": filename, "mime": mime})

        if not files:
            await status.edit("No files found")
            shutil.rmtree(extract_to, ignore_errors=True)
            os.remove(path)
            return

        active_uploads[user_id] = len(files)
        await status.edit(
            f"📤 **Uploading {len(files)} files from `{zip_name}`...**",
            buttons=[[Button.inline("❌ Cancel", b"cancel")]],
        )

        # Send ZIP header
        await event.reply(f"📦 **Files from `{zip_name}`:**")

        sent = 0
        media_group = []

        for file in files:
            if pending_tasks.get(user_id):
                await status.edit("🛑 Cancelled")
                break

            if file["mime"].startswith("video/"):
                add_silent_audio(file["path"], muted_videos)  # Collect, no notify

            uploaded = await fast_upload(client, file["path"])
            sent += 1
            active_uploads[user_id] = len(files) - sent
            await update_progress(status, sent, len(files), "Uploading", zip_name)

            caption = f"From `{zip_name}`: {file['name']}"

            if file["mime"].startswith(("image/", "video/")):
                if file["mime"].startswith("video/"):
                    meta = json.loads(
                        subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_entries",
                                "stream=width,height,duration",
                                "-of",
                                "json",
                                file["path"],
                            ],
                            capture_output=True,
                            text=True,
                        ).stdout
                    )["streams"][0]
                    media_group.append(
                        InputMediaUploadedDocument(
                            file=uploaded,
                            mime_type=file["mime"],
                            attributes=[
                                DocumentAttributeVideo(
                                    duration=int(float(meta.get("duration", 0))),
                                    w=int(meta.get("width", 0)),
                                    h=int(meta.get("height", 0)),
                                    supports_streaming=True,
                                )
                            ],
                        )
                    )
                else:
                    media_group.append(uploaded)
            else:
                await client.send_file(event.chat_id, uploaded, caption=caption)

            if len(media_group) == 10:
                await client.send_file(event.chat_id, media_group)
                media_group = []

        if media_group:
            await client.send_file(event.chat_id, media_group)

        # Send FINAL completion message (separate from status)
        final_msg = f"✅ **Upload Complete for `{zip_name}`!**\n\n"
        final_msg += f"📁 **Files Processed:** {len(files)}\n"
        if muted_videos:
            final_msg += f"🔇 **Silent Videos Protected:** {len(muted_videos)} (added silent track to prevent GIF conversion)\n"
            final_msg += "**Protected:** " + ", ".join(muted_videos) + "\n"
        final_msg += f"🎉 Thanks for using @{DEVELOPER}'s bot! ❤️"

        await event.reply(final_msg)

        # Keep status as progress reference
        await status.edit(f"✅ **Completed `{zip_name}`** (see above)")

        logger.info(
            f"User {user_id} finished `{zip_name}` - {len(files)} files, {len(muted_videos)} muted fixed"
        )

    except Exception as e:
        logger.error(f"Process error for {zip_name}: {e}")
        await status.edit(f"❌ Error: {str(e)}")
    finally:
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        pending_tasks.pop(user_id, None)
        active_uploads.pop(user_id, None)
        if user_active_tasks.get(user_id):
            user_active_tasks[user_id].cancel()


# ============================= COMMANDS & BUTTONS =============================
@client.on(events.NewMessage(pattern="/start"))
async def start(e) -> None:
    uptime = str(datetime.now() - start_time).split(".")[0]
    await e.reply(
        f"**✨ Premium Unzip Bot**\n**@{DEVELOPER}**\n\n"
        f"🟢 **Uptime:** `{uptime}`\n\n"
        "Send any archive (ZIP • RAR • 7Z • TAR)\n"
        "• Password protected supported\n"
        "• Muted videos auto-fixed\n"
        "• Mixed albums (photo + video)\n"
        "• **Per-user queue** (1 ZIP at a time per user)\n\n"
        "Ready! 🚀",
        buttons=[
            [
                Button.inline("📊 Status", b"status"),
                Button.inline("ℹ️ Help", b"help"),
                Button.inline("⏱ Uptime", b"uptime"),
            ],
            [Button.url("Developer", f"https://t.me/{DEVELOPER}")],
        ],
    )


@client.on(events.CallbackQuery(data=b"status"))
async def cb_status(e) -> None:
    user_id = e.sender_id
    queue_pos = len(user_queues[user_id]) if user_id in user_queues else 0
    active = active_uploads.get(user_id, 0)
    msg = f"👤 **Your Status:**\n"
    if active > 0:
        msg += f"📤 Active: {active} files\n"
    if queue_pos > 0:
        msg += f"⏳ Queued ZIPs: {queue_pos}\n"
    await e.answer(msg, alert=True)


@client.on(events.CallbackQuery(data=b"help"))
async def cb_help(e) -> None:
    await e.reply(
        "**Help Menu**\n\n"
        "• Send ZIP/RAR/7Z/TAR file\n"
        "• Password: `/pass abc123` then resend\n"
        "• Queue: 1 ZIP per user at a time\n"
        "• Cancel: Stops your current ZIP\n"
        "• Muted videos fixed automatically\n\n"
        "Supported: All files (PDF, DOC, videos, etc.)\n"
        "Made by @hellopeter3 ❤️"
    )


@client.on(events.CallbackQuery(data=b"uptime"))
async def cb_uptime(e) -> None:
    await e.answer(
        f"Uptime: {str(datetime.now() - start_time).split('.')[0]}", alert=True
    )


@client.on(events.CallbackQuery(data=b"cancel"))
async def cancel(e) -> None:
    user_id = e.sender_id
    if user_id in pending_tasks:
        pending_tasks[user_id] = True
        await e.answer("Cancelled your active ZIP!", alert=True)
    if user_id in user_queues and user_queues[user_id]:
        user_queues[user_id].popleft()
        await e.answer("Removed your next ZIP from queue!", alert=True)
    logger.info(f"User {user_id} cancelled task")


@client.on(events.NewMessage(pattern="/status"))
async def cmd_status(e) -> None:
    user_id = e.sender_id
    queue_pos = len(user_queues[user_id]) if user_id in user_queues else 0
    active = active_uploads.get(user_id, 0)
    msg = f"👤 **Your Status:**\n"
    if active > 0:
        msg += f"📤 Active ZIP: {active} files\n"
    if queue_pos > 0:
        msg += f"⏳ Queued ZIPs: {queue_pos}\n"
    await e.reply(msg)


@client.on(events.NewMessage(pattern="/uptime"))
async def cmd_uptime(e) -> None:
    await e.reply(f"⏱ **Uptime:** {str(datetime.now() - start_time).split('.')[0]}")


@client.on(events.NewMessage(pattern="/pass"))
async def set_pass(e) -> None:
    parts = e.text.split()
    if len(parts) < 2:
        await e.reply("Usage: `/pass your_password`")
        return
    user_passwords[e.sender_id] = " ".join(parts[1:]).encode()
    await e.reply("✅ Password saved! Resend the file.")
    logger.info(f"Password set by {e.sender_id}")


# ============================= MAIN HANDLER =============================
@client.on(
    events.NewMessage(
        func=lambda e: e.file
        and e.file.name
        and e.file.name.lower().endswith(
            (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2")
        )
    )
)
async def handle_archive(event) -> None:
    user_id = event.sender_id
    zip_name = event.file.name

    # Check if user has active task
    if user_id in user_active_tasks and not user_active_tasks[user_id].done():
        await event.reply("⚠️ You already have an active ZIP! Wait or cancel it.")
        return

    # Add to per-user queue
    task_data = {
        "event": event,
        "status": None,  # Will be set after
        "user_id": user_id,
        "zip_name": zip_name,
    }
    if user_id not in user_queues:
        user_queues[user_id] = deque()
    user_queues[user_id].append(task_data)

    # Start processing if no active task for this user
    if len(user_queues[user_id]) == 1 and user_id not in user_active_tasks:
        # Create status message
        status = await event.reply("🚀 **Starting your ZIP...**")
        task_data["status"] = status

        # Start the task
        task = asyncio.create_task(process_archive(task_data))
        user_active_tasks[user_id] = task
        task.add_done_callback(lambda t: user_active_tasks.pop(user_id, None))

    else:
        # Already queued, inform position
        pos = len(user_queues[user_id])
        await event.reply(f"📥 **Added to your queue** (Position {pos})")

    logger.info(
        f"User {user_id} queued `{zip_name}` (user queue len: {len(user_queues[user_id])})"
    )


# ============================= START =============================
async def main() -> None:
    await client.start(bot_token=BOT_TOKEN)

    logger.info(f"{c.C}Simple queue started (1 ZIP per user){c.E}")

    print(f"{c.G}BOT BY @{DEVELOPER} IS 100% READY & ONLINE!{c.E}")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
