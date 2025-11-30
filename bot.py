# bot.py - PREMIUM UNZIP BOT by @hellopeter3
import asyncio
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import re  # For manual pattern matching in callbacks
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
user_in_queue: set[int] = set()  # Fast check for users in queue (prevents multi-add)
cancelled_users: set[int] = set()  # Renamed for clarity
user_task_data: dict[int, dict] = {}  # Store task reference per user
# cancelled_tasks: set[int] = set()  # Track cancelled task IDs to skip in queue


# ============================= HELPER: Check if Cancelled =============================
def is_cancelled(user_id: int) -> bool:
    """Unified cancel check - use this everywhere"""
    return pending_tasks.get(user_id, False) or user_id in cancelled_users


# ============================= QUEUE SYSTEM =============================
task_queue = asyncio.Queue()
queue_list = []  # Track queue for status display
is_processing = False
current_user = None
current_archive = None  # Track current processing archive name


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


def add_silent_audio(path: str, muted_list: list[str]) -> None:
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
    msg, cur: int, total: int, action: str = "Processing", user_id: int = None
) -> None:
    """Progress updater with cancel check"""
    try:
        # Check cancel during progress updates
        if user_id and is_cancelled(user_id):
            return  # Stop updating if cancelled

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
    global is_processing, current_user, current_archive, user_in_queue, cancelled_users

    while True:
        task_data = await task_queue.get()
        user_id = task_data["user_id"]

        # Check cancelled_users set before starting
        if user_id in cancelled_users:
            logger.info(f"Skipping cancelled task for user {user_id}")
            queue_list[:] = [q for q in queue_list if q["user_id"] != user_id]
            cancelled_users.discard(user_id)
            task_queue.task_done()
            user_in_queue.discard(user_id)
            continue

        is_processing = True
        current_user = user_id
        current_archive = task_data["event"].file.name
        user_in_queue.discard(user_id)

        # Store task reference
        user_task_data[user_id] = task_data

        try:
            await process_archive(task_data)
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            try:
                await task_data["status"].edit(f"❌ Error: {str(e)}")
            except:
                pass
        finally:
            # Cleanup
            queue_list[:] = [q for q in queue_list if q["user_id"] != user_id]
            is_processing = False
            current_user = None
            current_archive = None
            user_task_data.pop(user_id, None)
            pending_tasks.pop(user_id, None)
            task_queue.task_done()


# ============================= PROCESS ARCHIVE (Main Logic) =============================
async def process_archive(task_data):
    """Main processing function with comprehensive cancel checks"""
    event = task_data["event"]
    status = task_data["status"]
    user_id = task_data["user_id"]

    # Initialize cancel flag
    pending_tasks[user_id] = False
    active_uploads[user_id] = 0

    archive_name = event.file.name
    muted_videos = []

    # === DOWNLOAD PHASE ===
    await status.edit(
        "⬇️ **Downloading...**", buttons=[[Button.inline("❌ Cancel", b"cancel")]]
    )

    path = f"downloads/{event.file.name}"
    os.makedirs("downloads", exist_ok=True)

    # Proper async wrapper that FastTelethon can call
    async def download_progress(current, total):
        if is_cancelled(user_id):
            raise asyncio.CancelledError("Download cancelled by user")
        await update_progress(status, current, total, "Downloading", user_id)
        return None  # Important: must return None or the progress continues

    try:
        await fast_download(
            client,
            event.message,
            path,
            progress_bar_function=download_progress,
        )
    except asyncio.CancelledError:
        await status.edit("🛑 **Download cancelled!**")
        if os.path.exists(path):
            os.remove(path)
        return
    except Exception as e:
        await status.edit(f"❌ Download failed: {str(e)}")
        return

    # Check after download
    if is_cancelled(user_id):
        await status.edit("🛑 **Cancelled after download!**")
        if os.path.exists(path):
            os.remove(path)
        return

    # === EXTRACTION PHASE ===
    extract_to = path + "_extracted"
    os.makedirs(extract_to, exist_ok=True)

    await status.edit(
        "🔓 **Extracting...**", buttons=[[Button.inline("❌ Cancel", b"cancel")]]
    )

    if is_cancelled(user_id):
        await status.edit("🛑 **Extraction cancelled!**")
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    result = extract_archive(path, extract_to, user_passwords.get(user_id))

    if result == "password_required":
        await status.edit("🔒 **Password required!**\nSend `/pass your_password`")
        user_passwords.pop(user_id, None)
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    if result != "success":
        await status.edit(f"❌ Failed: {result}")
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    # Check after extraction
    if is_cancelled(user_id):
        await status.edit("🛑 **Cancelled after extraction!**")
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    # === COLLECT FILES ===
    files = []
    images_count = 0
    videos_count = 0

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

            if mime.startswith("image/"):
                images_count += 1
            elif mime.startswith("video/"):
                videos_count += 1

    if not files:
        await status.edit("❌ No files found in archive")
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    # === UPLOAD PHASE ===
    active_uploads[user_id] = len(files)

    await status.edit(
        f"📤 **Uploading {len(files)} files...**",
        buttons=[[Button.inline("❌ Cancel", b"cancel")]],
    )

    if is_cancelled(user_id):
        await status.edit("🛑 **Cancelled before upload!**")
        shutil.rmtree(extract_to, ignore_errors=True)
        if os.path.exists(path):
            os.remove(path)
        return

    # Send header
    await event.reply(f"📦 **Files from `{archive_name}`:**")

    sent = 0
    media_group = []

    for file in files:
        # CRITICAL: Check cancel before EACH file
        if is_cancelled(user_id):
            await status.edit("🛑 **Upload cancelled!**")
            await event.reply(
                f"⚠️ **Task cancelled. {sent}/{len(files)} files uploaded.**"
            )
            break

        # Process video audio
        if file["mime"].startswith("video/"):
            add_silent_audio(file["path"], muted_videos)

        # Use correct fast_upload without progress (FastTelethon doesn't support upload progress)
        try:
            uploaded = await fast_upload(client, file["path"])
        except Exception as e:
            logger.error(f"Upload error for {file['name']}: {e}")
            continue

        sent += 1
        active_uploads[user_id] = len(files) - sent

        # Update progress manually after each file
        await update_progress(status, sent, len(files), "Uploading", user_id)

        # Check again before sending
        if is_cancelled(user_id):
            await status.edit("🛑 **Send cancelled!**")
            break

        caption = f"From `{archive_name}`: {file['name']}"

        # Send file
        if file["mime"].startswith(("image/", "video/")):
            if file["mime"].startswith("video/"):
                try:
                    meta = json.loads(
                        subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-select_streams",
                                "v",
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
                except Exception as e:
                    logger.error(f"Video metadata error for {file['name']}: {e}")
                    # Fallback: send as regular file
                    await client.send_file(event.chat_id, uploaded, caption=caption)
                    continue
            else:
                media_group.append(uploaded)
        else:
            await client.send_file(event.chat_id, uploaded, caption=caption)

        # Send media group if full
        if len(media_group) == 10:
            if is_cancelled(user_id):
                break
            try:
                await client.send_file(event.chat_id, media_group)
            except Exception as e:
                logger.error(f"Failed to send media group: {e}")
            media_group = []

        # Yield to event loop
        await asyncio.sleep(0.05)

    # Send remaining media
    if media_group and not is_cancelled(user_id):
        try:
            await client.send_file(event.chat_id, media_group)
        except Exception as e:
            logger.error(f"Failed to send remaining media group: {e}")

    # === COMPLETION ===
    if is_cancelled(user_id):
        # Already notified above
        pass
    else:
        # Success message
        final_msg = f"✅ **Upload Complete for `{archive_name}`!**\n\n"
        final_msg += f"📁 **Total Files:** {len(files)}\n"
        final_msg += f"🖼️ **Images:** {images_count}\n"
        final_msg += f"🎥 **Videos:** {videos_count}\n"

        if muted_videos:
            final_msg += f"🔇 **Silent Videos Fixed:** {len(muted_videos)}\n"

        final_msg += f"\n🎉 Thanks for using @{DEVELOPER}'s bot!"

        await event.reply(final_msg)
        await status.edit(f"✅ **Completed `{archive_name}`**")

        logger.info(
            f"User {user_id} completed - {len(files)} files from {archive_name}"
        )

    # Cleanup
    shutil.rmtree(extract_to, ignore_errors=True)
    if os.path.exists(path):
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
    user_id = e.sender_id
    if not is_processing and not queue_list:
        await e.answer("✅ No active tasks", alert=True)
        return
    # Build status with positions and filenames
    status_text = "📊 **Queue Status:**\n\n"
    if is_processing:
        status_text += f"🔄 **Processing:** `{current_archive or 'Unknown'}`\n\n"
    if queue_list:
        status_text += "**Upcoming:**\n"
        for i, task in enumerate(queue_list, 1):
            filename = task["event"].file.name
            status_text += f"{i}. `{filename}`\n"
    await e.answer(status_text, alert=True)


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


# Generic CallbackQuery handler + manual re.match for cancel patterns
@client.on(events.CallbackQuery())
async def cancel_specific(e) -> None:
    """Handle specific position cancels"""
    data_bytes = e.data
    if not data_bytes or not data_bytes.startswith(b"cancel_"):
        return

    data = data_bytes.decode()
    user_id = e.sender_id

    match = re.match(r"cancel_(all|pos_(\d+))", data)
    if not match:
        return

    action = match.group(1)

    if action == "all":
        # Cancel all user's tasks
        user_tasks = [q for q in queue_list if q["user_id"] == user_id]
        if not user_tasks:
            await e.answer("No tasks to cancel.", alert=True)
            return

        queue_list[:] = [q for q in queue_list if q["user_id"] != user_id]
        cancelled_users.add(user_id)
        user_in_queue.discard(user_id)

        await e.answer(f"🗑️ Cancelled {len(user_tasks)} queued task(s)!", alert=True)
        logger.info(f"User {user_id} cancelled all queued tasks")
        return

    # Cancel specific position
    pos = int(match.group(2))
    if 1 <= pos <= len(queue_list):
        task = queue_list[pos - 1]
        if task["user_id"] == user_id:
            filename = task["event"].file.name
            del queue_list[pos - 1]
            cancelled_users.add(user_id)
            user_in_queue.discard(user_id)

            await e.answer(f"❌ Cancelled: `{filename}`", alert=True)
            logger.info(f"User {user_id} cancelled position {pos}")
        else:
            await e.answer("⚠️ Cannot cancel others' tasks.", alert=True)
    else:
        await e.answer("Invalid position.", alert=True)


@client.on(events.CallbackQuery(data=b"cancel"))
async def cancel(e) -> None:
    """Handle cancel button in status message"""
    user_id = e.sender_id

    # Cancel active task
    if user_id in pending_tasks or user_id == current_user:
        pending_tasks[user_id] = True
        await e.answer("🛑 Cancelling current task...", alert=True)
        logger.info(f"User {user_id} pressed cancel button")
        return

    # Cancel queued task
    if user_id in user_in_queue:
        cancelled_users.add(user_id)
        queue_list[:] = [q for q in queue_list if q["user_id"] != user_id]
        user_in_queue.discard(user_id)
        await e.answer("🛑 Queued task cancelled!", alert=True)
        logger.info(f"User {user_id} cancelled queued task")
        return

    await e.answer("No active task to cancel.", alert=False)


# Text commands
@client.on(events.NewMessage(pattern="/status"))
async def cmd_status(e) -> None:
    """Show queue status with cancel options"""
    user_id = e.sender_id

    if not is_processing and not queue_list:
        await e.reply("✅ **No active tasks**")
        return

    status_text = "📊 **Queue Status:**\n\n"
    buttons = []

    if is_processing and current_user:
        status_text += f"🔄 **Processing:** `{current_archive or 'Unknown'}`"
        if current_user == user_id:
            status_text += " **(Your task)**"
            buttons.append([Button.inline("❌ Cancel Current", b"cancel")])
        status_text += "\n\n"

    if queue_list:
        status_text += "**Upcoming Queue:**\n"
        user_positions = []

        for i, task in enumerate(queue_list, 1):
            filename = task["event"].file.name
            is_yours = task["user_id"] == user_id
            marker = "👤 " if is_yours else ""
            status_text += f"{i}. {marker}`{filename}`\n"

            if is_yours:
                user_positions.append(i)

        # Add cancel buttons for user's tasks
        if user_positions:
            if len(user_positions) == 1:
                buttons.append(
                    [
                        Button.inline(
                            "❌ Cancel My Task",
                            f"cancel_pos_{user_positions[0]}".encode(),
                        )
                    ]
                )
            else:
                buttons.append([Button.inline("🗑️ Cancel All Mine", b"cancel_all")])

    if buttons:
        await e.reply(status_text, buttons=buttons)
    else:
        await e.reply(status_text)


@client.on(events.NewMessage(pattern="/cancel"))
async def cmd_cancel(e) -> None:
    """Handle /cancel command"""
    user_id = e.sender_id

    # Cancel active processing task
    if user_id in pending_tasks or user_id == current_user:
        pending_tasks[user_id] = True
        await e.reply("🛑 **Cancelling current task...**")
        logger.info(f"User {user_id} used /cancel on active task")
        return

    # Cancel queued tasks
    user_tasks = [q for q in queue_list if q["user_id"] == user_id]
    if not user_tasks:
        await e.reply("ℹ️ **No tasks to cancel.** Use `/status` to check queue.")
        return

    if len(user_tasks) == 1:
        # Cancel single task
        filename = user_tasks[0]["event"].file.name
        queue_list[:] = [q for q in queue_list if q["user_id"] != user_id]
        cancelled_users.add(user_id)
        user_in_queue.discard(user_id)

        await e.reply(f"❌ **Cancelled:** `{filename}`")
        logger.info(f"User {user_id} cancelled queued task")
    else:
        # Show options for multiple
        buttons = []
        for i, task in enumerate(user_tasks, 1):
            filename = task["event"].file.name
            buttons.append(
                [
                    Button.inline(
                        f"❌ Cancel: {filename[:30]}", f"cancel_pos_{i}".encode()
                    )
                ]
            )
        buttons.append([Button.inline("🗑️ Cancel All", b"cancel_all")])

        await e.reply(
            f"**You have {len(user_tasks)} queued tasks. Choose:**", buttons=buttons
        )


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

    # Use set for fast, atomic check to prevent multi-adds
    global user_in_queue
    if user_id in user_in_queue:
        await event.reply("⚠️ You already have a file in queue! Please wait.")
        return

    queue_position = len(queue_list) + (
        1 if is_processing else 0
    )  # Accurate pos with processing
    if queue_position == 1 and not is_processing:
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
        "cancelled": False,  # Track per-task cancel
    }

    queue_list.append(task_data)
    user_in_queue.add(user_id)  # Mark user as queued
    await task_queue.put(task_data)
    logger.info(
        f"User {user_id} added to queue (position: {queue_position}, file: {event.file.name})"
    )


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
