# bot.py - PREMIUM UNZIP BOT by @hellopeter3
import asyncio
import json
import logging
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime

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
user_passwords: dict[int, bytes] = {}
pending_tasks: dict[int, bool] = {}
active_uploads: dict[int, int] = {}

# ============================= QUEUE SYSTEM =============================
task_queue = asyncio.Queue()
queue_list = []  # Track queue for status display
is_processing = False
current_user = None


# ============================= MUTED VIDEO  =============================
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


def add_silent_audio(path: str, event) -> None:
    name = os.path.basename(path)
    if video_has_audio(path):
        logger.info(f"{c.G}Audio preserved → {name}{c.E}")
        return
    logger.info(f"{c.Y}MUTED VIDEO → Adding silent audio: {name}{c.E}")
    asyncio.create_task(
        event.respond(
            f"🔇 **Muted video fixed!**\n`{name}`\nNow it won't become GIF 🎉"
        )
    )
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
    msg, cur: int, total: int, action: str = "Processing"
) -> None:
    try:
        bar = "█" * int(20 * cur / total) + "░" * (20 - int(20 * cur / total))
        perc = cur / total * 100
        await msg.edit(
            f"**{action}**\n\n`{bar}` {perc:.1f}%\n{cur:,} / {total:,} files",
            buttons=[[Button.inline("❌ Cancel", b"cancel")]],
        )
    except Exception:
        pass


# ============================= QUEUE WORKER =============================
async def queue_worker():
    """Process one task at a time from the queue"""
    global is_processing, current_user
    
    while True:
        task_data = await task_queue.get()
        is_processing = True
        current_user = task_data["user_id"]
        
        try:
            await process_archive(task_data)
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            try:
                await task_data["status"].edit(f"❌ Error: {str(e)}")
            except:
                pass
        finally:
            # Remove from queue list
            queue_list[:] = [q for q in queue_list if q["user_id"] != task_data["user_id"]]
            is_processing = False
            current_user = None
            task_queue.task_done()


# ============================= PROCESS ARCHIVE (Main Logic) =============================
async def process_archive(task_data):
    """Main processing function - extracted from handle_archive"""
    event = task_data["event"]
    status = task_data["status"]
    user_id = task_data["user_id"]
    
    pending_tasks[user_id] = False
    active_uploads[user_id] = 0

    # Store archive name for final message
    archive_name = event.file.name

    await status.edit(
        "⬇️ **Downloading...**", buttons=[[Button.inline("❌ Cancel", b"cancel")]]
    )
    path = f"downloads/{event.file.name}"
    os.makedirs("downloads", exist_ok=True)

    try:
        await fast_download(
            client,
            event.message,
            path,
            progress_bar_function=lambda c, t: asyncio.create_task(
                update_progress(status, c, t, "Downloading")
            ),
        )
    except:
        await status.edit("Download cancelled/failed")
        return

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
        f"📤 **Uploading {len(files)} files...**",
        buttons=[[Button.inline("❌ Cancel", b"cancel")]],
    )

    sent = 0
    media_group = []

    for file in files:
        if pending_tasks.get(user_id):
            await status.edit("🛑 Cancelled")
            break

        if file["mime"].startswith("video/"):
            add_silent_audio(file["path"], event)

        uploaded = await fast_upload(client, file["path"])
        sent += 1
        active_uploads[user_id] = len(files) - sent
        await update_progress(status, sent, len(files), "Uploading")

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
            await client.send_file(event.chat_id, uploaded, caption=file["name"])

        if len(media_group) == 10:
            await client.send_file(event.chat_id, media_group)
            media_group = []

    if media_group:
        await client.send_file(event.chat_id, media_group)

    # Modified completion message with archive name
    await status.edit(
        f"✅ **Extraction Complete!**\n\n"
        f"📦 **Archive:** `{archive_name}`\n"
        f"📁 **Files Extracted:** {len(files)}\n\n"
        f"Thanks for using @{DEVELOPER}'s bot ❤️"
    )
    logger.info(f"User {user_id} finished - {len(files)} files from {archive_name}")

    shutil.rmtree(extract_to, ignore_errors=True)
    os.remove(path)
    pending_tasks.pop(user_id, None)
    active_uploads.pop(user_id, None)


# ============================= COMMANDS & BUTTONS =============================
@client.on(events.NewMessage(pattern="/start"))
async def start(e) -> None:
    uptime = str(datetime.now() - start_time).split(".")[0]
    await e.reply(
        f"**✨ Premium Unzip Bot**\n**@{DEVELOPER}**\n\n"
        f"🟢 **Uptime:** `{uptime}`\n\n"
        "Send any archive (ZIP • RAR • 7Z)\n"
        "Support password protected files\n"
        "Support All files(pdf, csv, xml, etc)\n"
        "•Mixed albums\n"
        "•**Queue system: 1 zip at a time**\n\n"
        "Ready!",
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
    if not is_processing and task_queue.empty():
        await e.answer("✅ No active tasks", alert=True)
    else:
        queue_size = task_queue.qsize()
        msg = f"⚙️ Processing: User {current_user}\n📋 In queue: {queue_size}"
        await e.answer(msg, alert=True)


@client.on(events.CallbackQuery(data=b"help"))
async def cb_help(e) -> None:
    await e.reply(
        "**Help**\n• Send archive\n• Password: `/pass abc123`\n• Cancel anytime\n"
        "• **Queue: Files processed one at a time**\n\n"
        "Supported: ZIP RAR 7Z TAR\nMade by @hellopeter3"
    )


@client.on(events.CallbackQuery(data=b"uptime"))
async def cb_uptime(e) -> None:
    await e.answer(str(datetime.now() - start_time).split(".")[0], alert=True)


@client.on(events.CallbackQuery(data=b"cancel"))
async def cancel(e) -> None:
    if e.sender_id in pending_tasks:
        pending_tasks[e.sender_id] = True
        await e.answer("Cancelled!", alert=True)


# Text commands
@client.on(events.NewMessage(pattern="/status"))
async def cmd_status(e) -> None:
    if not is_processing and task_queue.empty():
        await e.reply("✅ **No active tasks**")
    else:
        queue_size = task_queue.qsize()
        msg = f"⚙️ **Currently processing**\n📋 **Queue:** {queue_size} waiting"
        if e.sender_id in [q["user_id"] for q in queue_list]:
            pos = [i for i, q in enumerate(queue_list) if q["user_id"] == e.sender_id][0]
            msg += f"\n\n**Your position:** {pos + 1}"
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


# ============================= MAIN HANDLER (Queue Entry) =============================
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
    
    # Check if user already in queue
    if any(q["user_id"] == user_id for q in queue_list):
        await event.reply("⚠️ You already have a file in queue! Please wait.")
        return
    
    queue_position = task_queue.qsize() + 1
    
    if queue_position == 1:
        status = await event.reply("🚀 **Starting immediately...**")
    else:
        status = await event.reply(
            f"📥 **Added to queue**\n**Position:** {queue_position}\n\n"
            f"Processing 1 file at a time. Please wait..."
        )
    
    task_data = {
        "event": event,
        "status": status,
        "user_id": user_id,
    }
    
    queue_list.append(task_data)
    await task_queue.put(task_data)
    logger.info(f"User {user_id} added to queue (position: {queue_position})")


# ============================= START =============================
async def main() -> None:
    await client.start(bot_token=BOT_TOKEN)
    
    # Start queue worker
    asyncio.create_task(queue_worker())
    logger.info(f"{c.C}Queue worker started{c.E}")
    
    print(f"{c.G}BOT BY @{DEVELOPER} IS 100% READY & ONLINE!{c.E}")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
