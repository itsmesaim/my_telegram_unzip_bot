# bot/main.py
import os
import zipfile
import shutil
import logging
import mimetypes
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeVideo,
    DocumentAttributeImageSize,
    DocumentAttributeFilename,
    InputMediaUploadedDocument,
)
from FastTelethonhelper import fast_download, fast_upload
import subprocess
import json
import asyncio
from typing import List, Dict, Any, Tuple, Optional

# Load environment variables
load_dotenv()

# Initialize logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("BotLogger")

# Telegram API credentials (validate & cast)
API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

try:
    API_ID = int(API_ID_RAW) if API_ID_RAW is not None else None
except ValueError:
    API_ID = None

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.error("Missing TELEGRAM credentials. Make sure API_ID, API_HASH and BOT_TOKEN are set.")
    raise SystemExit("Missing TELEGRAM credentials. Exiting.")

# Load session string from file if available
SESSION_FILE = "session_save.txt"
if os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "r") as file:
        SESSION_STRING = file.read().strip()
else:
    SESSION_STRING = ""

# Create client after validating credentials
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Constants
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_GROUP_SIZE = 10  # Maximum files per group/album
MAX_CAPTION_LENGTH = 1024  # Telegram caption limit
start_time = datetime.now()

# Task Queue and Active Tasks
task_queue: asyncio.Queue = asyncio.Queue()
active_tasks: Dict[int, Dict[str, Any]] = {}  # {user_id: {'task': asyncio.Task, 'cancel_flag': bool, 'zip_name': str}}

# User preferences storage (in production, use a database)
user_preferences: Dict[int, Dict[str, Any]] = {}


async def process_tasks():
    """
    Process tasks from the task queue one at a time.
    """
    while True:
        task_data = await task_queue.get()
        # task_data expected to be a tuple like (event,)
        event = task_data[0]
        user_id = getattr(event, "chat_id", None)

        try:
            # Create cancellable task
            task_coro = handle_zip_task(event)
            task = asyncio.create_task(task_coro)

            # Safe zip name extraction
            zip_name_safe = "<unknown>"
            try:
                zip_name_safe = getattr(event.message.file, "name", "<unknown>")
            except Exception:
                pass

            active_tasks[user_id] = {
                "task": task,
                "cancel_flag": False,
                "zip_name": zip_name_safe,
            }

            await task

        except asyncio.CancelledError:
            logger.info(f"Task cancelled for user {user_id}")
            try:
                await event.respond("✅ **Upload cancelled successfully.**")
            except Exception:
                pass
        except Exception as e:
            logger.exception(f"Error processing task: {e}")
            try:
                await event.respond(f"❌ **Error:** {str(e)}")
            except Exception:
                pass
        finally:
            # Clean up task reference
            if user_id in active_tasks:
                del active_tasks[user_id]
            task_queue.task_done()

# --- Added: extended archive support and per-user upload mode/password handling ---
try:
    import pyzipper
except Exception:
    pyzipper = None

try:
    import rarfile
except Exception:
    rarfile = None

import tarfile
try:
    import py7zr
except Exception:
    py7zr = None

from typing import Tuple

# Per-user settings
user_modes = {}          # user_id -> "media" or "doc"
user_passwords = {}      # user_id -> bytes
pending_extractions = {} # user_id -> dict(zip_path, unzip_dir, file_name, progress_msg_id)

def extract_archive_sync(file_path: str, unzip_dir: str, user_id: int) -> Tuple[bool, str]:

    password = user_passwords.get(user_id)
    try:
        # ZIP (including ZIP64, encrypted)
        if zipfile.is_zipfile(file_path):
            if pyzipper is not None:
                with pyzipper.AESZipFile(file_path) as zf:
                    namelist = zf.namelist()
                    encrypted = any((zi.flag_bits & 0x1) for zi in zf.infolist())
                    if encrypted and not password:
                        return False, "NEED_PASSWORD"
                    for member in namelist:
                        if member.endswith('/'):
                            os.makedirs(os.path.join(unzip_dir, member), exist_ok=True)
                            continue
                        try:
                            if password:
                                zf.extract(member, path=unzip_dir, pwd=password)
                            else:
                                zf.extract(member, path=unzip_dir)
                        except RuntimeError as e:
                            if "password" in str(e).lower():
                                return False, "NEED_PASSWORD"
                            raise
            else:
                with zipfile.ZipFile(file_path, "r") as zf:
                    namelist = zf.namelist()
                    encrypted = any((zi.flag_bits & 0x1) for zi in zf.infolist())
                    if encrypted and not password:
                        return False, "NEED_PASSWORD"
                    for member in namelist:
                        if member.endswith('/'):
                            os.makedirs(os.path.join(unzip_dir, member), exist_ok=True)
                            continue
                        if password:
                            zf.extract(member, path=unzip_dir, pwd=password)
                        else:
                            zf.extract(member, path=unzip_dir)
            return True, None

        # RAR
        if rarfile is not None and rarfile.is_rarfile(file_path):
            try:
                with rarfile.RarFile(file_path) as rf:
                    needs_pw = False
                    try:
                        needs_pw = getattr(rf, "needs_password", lambda: False)()
                    except Exception:
                        needs_pw = False
                    if needs_pw and not password:
                        return False, "NEED_PASSWORD"
                    rf.extractall(path=unzip_dir, pwd=password.decode() if password else None)
                return True, None
            except Exception as e:
                msg = str(e)
                if "No such file or directory" in msg or "unrar" in msg.lower() or "bsdtar" in msg.lower():
                    return False, "❌ RAR extraction failed: required system helper (unrar/bsdtar) not found. Install `unrar` or `bsdtar`."
                return False, f"❌ RAR extraction failed: {e}"

        # TAR (tar, tar.gz, tgz, tar.bz2)
        if tarfile.is_tarfile(file_path):
            with tarfile.open(file_path) as tf:
                tf.extractall(path=unzip_dir)
            return True, None

        # 7Z
        if py7zr is not None and py7zr.is_7zfile(file_path):
            with py7zr.SevenZipFile(file_path, mode="r", password=password.decode() if password else None) as z7:
                z7.extractall(path=unzip_dir)
            return True, None

        return False, "⚠️ Unsupported archive format."
    except RuntimeError as e:
        return False, f"❌ Extraction failed: {e}"
    except Exception as e:
        return False, f"❌ Extraction failed: {e}"

async def process_and_upload(event, unzip_dir: str, zip_path: str, file_name: str, progress_msg, user_id: int):
    """
    Reusable logic: prepare files_to_upload and call existing upload routines.
    This is mostly lifted from the original code to avoid duplication.
    """
    files_to_upload = []
    for root, _, files in os.walk(unzip_dir):
        for filename in files:
            await check_cancel_flag(user_id)
            file_path = os.path.join(root, filename)
            f_size = os.path.getsize(file_path)
            if f_size > MAX_FILE_SIZE:
                await event.respond(f"⚠️ Skipping `{filename}`: Exceeds 2GB limit")
                continue
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            # honor per-user mode: 'doc' forces non-media upload
            if user_modes.get(user_id) == "doc":
                mime_type = "application/octet-stream"
            files_to_upload.append(
                {"path": file_path, "name": filename, "size": f_size, "mime_type": mime_type}
            )

    # No files found
    if not files_to_upload:
        try:
            await progress_msg.edit("⚠️ No files were extracted from the archive.")
        except Exception:
            pass
        cleanup(unzip_dir, zip_path)
        return

    # Determine user preferences (fallback to defaults in original user_preferences dict)
    prefs = user_preferences.get(user_id, {"group_upload": True, "group_size": 10})

    # Upload files using the existing helpers
    if prefs.get("group_upload", True):
        await upload_files_grouped(event, files_to_upload, progress_msg, file_name, prefs.get("group_size", 10), user_id)
    else:
        await upload_files_individual(event, files_to_upload, progress_msg, file_name, user_id)

    cleanup(unzip_dir, zip_path)

    # Send summary (keep message structure similar to original)
    try:
        summary_msg = (
            "✅ **Upload Complete!**\n\n"
            f"📁 **Source:** `{file_name}`\n"
            f"📊 **Files Processed:** {len(files_to_upload)}\n"
            f"🎯 **Upload Mode:** {'Documents only' if user_modes.get(user_id)=='doc' else 'Mixed/Auto'}\n"
        )
        await event.respond(summary_msg)
    except Exception:
        pass


@client.on(events.NewMessage(pattern=r"^/mode(?:$|\s)"))
async def set_mode_handler(event):
    parts = event.message.text.split()
    if len(parts) < 2 or parts[1].lower() not in ("media", "doc"):
        await event.respond("Usage: /mode <media|doc>")
        return
    mode = parts[1].lower()
    user_modes[event.chat_id] = mode
    await event.respond(f"✅ Upload mode set to *{mode}*")

@client.on(events.NewMessage(pattern=r"^/unzip(?:$|\s)"))
async def set_password_handler(event):
    parts = event.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await event.respond("Usage: /unzip <password> (saves password for pending password-protected archive)")
        return
    pw = parts[1].strip()
    user_passwords[event.chat_id] = pw.encode()
    await event.respond("🔑 Password saved for next extraction.")

    # If user had a pending extraction, try to resume
    pend = pending_extractions.pop(event.chat_id, None)
    if pend:
        try:
            await event.respond("🔄 Resuming extraction with provided password...")
            ok, err = extract_archive_sync(pend["zip_path"], pend["unzip_dir"], event.chat_id)
            if not ok:
                await event.respond(err or "❌ Extraction failed.")
                cleanup(pend["unzip_dir"], pend["zip_path"])
                return
            await process_and_upload(event, pend["unzip_dir"], pend["zip_path"], pend["file_name"], await event.respond("📂 Preparing uploads..."), event.chat_id)
        except Exception as e:
            await event.respond(f"❌ Failed to resume extraction: {e}")
            cleanup(pend.get("unzip_dir"), pend.get("zip_path"))

# --- End of added code ---


def get_video_metadata(file_path: str) -> Dict[str, int]:
    """
    Extract metadata from a video file using ffprobe.
    Returns dict with width, height, duration (seconds).
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration",
            "-of",
            "json",
            file_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or not result.stdout:
            logger.warning(f"ffprobe failed for {file_path}: {result.stderr.strip()}")
            return {"width": 0, "height": 0, "duration": 0}

        metadata = json.loads(result.stdout)
        stream = metadata.get("streams", [{}])[0]
        return {
            "width": int(stream.get("width", 0) or 0),
            "height": int(stream.get("height", 0) or 0),
            "duration": int(float(stream.get("duration", 0) or 0)),
        }
    except Exception as e:
        logger.error(f"Failed to extract metadata for {file_path}: {e}")
        return {"width": 0, "height": 0, "duration": 0}


def get_image_dimensions(file_path: str) -> Tuple[int, int]:
    """
    Get dimensions of an image file using ffprobe.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            file_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 or not result.stdout:
            logger.warning(f"ffprobe failed for image {file_path}: {result.stderr.strip()}")
            return 0, 0

        metadata = json.loads(result.stdout)
        stream = metadata.get("streams", [{}])[0]
        width = int(stream.get("width", 0) or 0)
        height = int(stream.get("height", 0) or 0)
        return width, height
    except Exception as e:
        logger.error(f"Failed to get image dimensions: {e}")
        return 0, 0


def get_video_thumbnail(file_path: str) -> Optional[str]:
    """
    Generate a thumbnail from a video file and return thumbnail path.
    """
    try:
        thumb_path = f"{file_path}_thumb.jpg"
        cmd = [
            "ffmpeg",
            "-i",
            file_path,
            "-ss",
            "00:00:01.000",
            "-vframes",
            "1",
            "-vf",
            "scale=320:-1",
            thumb_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumb_path):
            return thumb_path
        return None
    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {file_path}: {e}")
        return None


def add_silent_audio(file_path: str):
    """
    Add silent audio to a video file to prevent it from being uploaded as a GIF.
    Overwrites the original file with a temporary file (atomic replace).
    """
    try:
        temp_path = f"{file_path}_temp.mp4"
        cmd = [
            "ffmpeg",
            "-i",
            file_path,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            temp_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.replace(temp_path, file_path)
        logger.info(f"Added silent audio to {file_path}")
    except Exception as e:
        logger.error(f"Failed to add silent audio to {file_path}: {e}")
        # best-effort; do not raise here


def is_media_file(mime_type: Optional[str]) -> bool:
    """
    Check if a file is a media file (photo or video).
    """
    return bool(mime_type and (mime_type.startswith("image/") or mime_type.startswith("video/")))


def create_mixed_media_groups(files: List[Dict[str, Any]], max_group_size: int = MAX_GROUP_SIZE) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """
    Create groups mixing photos and videos together, with other files separated.
    Returns list of tuples: (group_type, [file_info,...]) where group_type is 'media' or 'documents'.
    """
    media_files = []
    other_files = []

    # Separate media from other files
    for file_info in files:
        if is_media_file(file_info.get("mime_type")):
            media_files.append(file_info)
        else:
            other_files.append(file_info)

    groups: List[Tuple[str, List[Dict[str, Any]]]] = []

    # Create mixed media groups
    if media_files:
        current_group: List[Dict[str, Any]] = []
        current_size = 0
        max_group_bytes = 50 * 1024 * 1024  # 50MB per group

        for file_info in media_files:
            file_size = file_info.get("size", 0)
            if (current_group and
                (len(current_group) >= max_group_size or
                 current_size + file_size > max_group_bytes)):
                groups.append(("media", current_group))
                current_group = []
                current_size = 0

            current_group.append(file_info)
            current_size += file_size

        if current_group:
            groups.append(("media", current_group))

    # Create groups for other files
    if other_files:
        current_group = []
        for file_info in other_files:
            current_group.append(file_info)
            if len(current_group) >= max_group_size:
                groups.append(("documents", current_group))
                current_group = []

        if current_group:
            groups.append(("documents", current_group))

    return groups


@client.on(events.NewMessage(pattern="/cancel"))
async def cancel_command(event):
    """
    Handles the /cancel command to stop ongoing uploads AND remove queued uploads belonging to the user.
    """
    user_id = event.chat_id

    removed_from_queue = 0

    # Remove queued tasks for this user from the queue
    try:
        old_queue = list(task_queue._queue)
        new_queue = [item for item in old_queue if getattr(item[0], "chat_id", None) != user_id]
        removed_from_queue = len(old_queue) - len(new_queue)
        # Replace queue contents
        task_queue._queue.clear()
        task_queue._queue.extend(new_queue)
    except Exception as e:
        logger.error(f"Failed to clean queue for user {user_id}: {e}")

    active_cancelled = False
    if user_id in active_tasks:
        task_info = active_tasks[user_id]
        task_info["cancel_flag"] = True

        # Cancel the task
        if task_info.get("task") and not task_info["task"].done():
            task_info["task"].cancel()
            active_cancelled = True
            logger.info(f"User {user_id} cancelled active upload of {task_info.get('zip_name')}")
    # Respond appropriately
    if active_cancelled and removed_from_queue:
        await event.respond(f"🛑 **Cancelled active upload** and removed **{removed_from_queue}** queued upload(s).")
    elif active_cancelled:
        await event.respond("🛑 **Cancelled active upload.**")
    elif removed_from_queue:
        await event.respond(f"🗑 **Removed {removed_from_queue} queued upload(s).**")
    else:
        await event.respond("ℹ️ **No active or queued uploads to cancel.**")


@client.on(events.NewMessage(pattern="/status"))
async def status_command(event):
    """
    Check the status of current upload and queued uploads for this user.
    """
    user_id = event.chat_id

    # Count queued uploads for user and find first queued position overall
    queued_positions = []
    overall_queue = list(task_queue._queue)
    for idx, item in enumerate(overall_queue):
        item_event = item[0] if isinstance(item, (list, tuple)) and item else None
        if item_event and getattr(item_event, "chat_id", None) == user_id:
            queued_positions.append(idx + 1)  # 1-based overall positions

    user_queued_count = len(queued_positions)
    if user_id not in active_tasks and user_queued_count == 0:
        await event.respond("ℹ️ **No active uploads or queued tasks.**")
        return

    if user_id in active_tasks:
        task_info = active_tasks[user_id]
        msg_lines = [
            "📊 **Upload Status**",
            "",
            f"📁 **Active File:** `{task_info.get('zip_name', '<unknown>')}`",
            "⚡ **Status:** Processing",
            f"🧾 **Queued uploads (you):** {user_queued_count}",
        ]
        if user_queued_count:
            msg_lines.append(f"🔢 **Your next queued item global position:** {queued_positions[0]}")
        msg_lines.append("\n🛑 Use /cancel to stop active upload and clear your queued uploads.")
        await event.respond("\n".join(msg_lines))
    else:
        # No active, but have queued items
        # Provide overall position of the first queued item and total queued for user
        first_pos = queued_positions[0] if queued_positions else None
        await event.respond(
            f"⏳ **Your upload is in queue**\n"
            f"Position (global): {first_pos}\n"
            f"Your queued uploads: {user_queued_count}\n\n"
            "🛑 Use /cancel to remove queued uploads."
        )


@client.on(events.NewMessage(pattern="/start"))
async def start_command(event):
    """
    Handles the /start command.
    """
    user_id = event.chat_id
    user_preferences[user_id] = {"group_upload": True, "group_size": 10}

    welcome_msg = (
        "👋 **Welcome to the Enhanced ZIP Uploader Bot!**\n\n"
        "Send me a ZIP file, and I'll upload its contents with these features:\n"
        "• 📦 **Mixed Media Groups**: Photos and videos uploaded together\n"
        "• 🛑 **Cancellable Uploads**: Stop uploads anytime with /cancel\n"
        "• 🎬 **Smart Processing**: Auto-optimization for all media\n"
        "• ⚡ **Queue System**: Multiple uploads per user are supported\n\n"
        "**Commands:**\n"
        "/start - Start the bot\n"
        "/help - Show help menu\n"
        "/cancel - Cancel current upload and clear queued uploads\n"
        "/status - Check upload status\n"
        "/settings - Configure preferences\n"
        "/uptime - Check bot uptime\n"
    )
    await event.respond(welcome_msg)
    logger.info(f"User {user_id} started the bot.")


@client.on(events.NewMessage(pattern="/settings"))
async def settings_command(event):
    """
    Handles the /settings command for user preferences.
    """
    user_id = event.chat_id
    prefs = user_preferences.get(user_id, {"group_upload": True, "group_size": 10})

    settings_msg = (
        "⚙️ **Upload Settings**\n\n"
        f"**Group Upload:** {'✅ Enabled' if prefs['group_upload'] else '❌ Disabled'}\n"
        f"**Files per Group:** {prefs['group_size']}\n"
        f"**Media Mixing:** ✅ Photos + Videos together\n\n"
        "**Commands:**\n"
        "/toggle_group - Enable/Disable group upload\n"
        "/set_group_size [1-10] - Set files per group\n"
    )
    await event.respond(settings_msg)


@client.on(events.NewMessage(pattern="/toggle_group"))
async def toggle_group_command(event):
    """
    Toggle group upload feature.
    """
    user_id = event.chat_id
    if user_id not in user_preferences:
        user_preferences[user_id] = {"group_upload": True, "group_size": 10}

    user_preferences[user_id]["group_upload"] = not user_preferences[user_id]["group_upload"]
    status = "Enabled" if user_preferences[user_id]["group_upload"] else "Disabled"
    await event.respond(f"✅ Group upload has been **{status}**")


@client.on(events.NewMessage(pattern="/set_group_size"))
async def set_group_size_command(event):
    """
    Set the number of files per group.
    """
    user_id = event.chat_id
    try:
        size = int(event.message.text.split()[1])
        if 1 <= size <= 10:
            if user_id not in user_preferences:
                user_preferences[user_id] = {"group_upload": True, "group_size": 10}
            user_preferences[user_id]["group_size"] = size
            await event.respond(f"✅ Group size set to **{size} files**")
        else:
            await event.respond("❌ Please choose a number between 1 and 10")
    except (IndexError, ValueError):
        await event.respond("❌ Usage: /set_group_size [1-10]")


@client.on(events.NewMessage(pattern="/help"))
async def help_command(event):
    """
    Handles the /help command.
    """
    help_msg = (
        "📜 **Help Menu**\n\n"
        "**Main Features:**\n"
        "• Send ZIP files up to 2GB\n"
        "• Mixed media groups (photos + videos)\n"
        "• Cancel uploads anytime\n"
        "• Smart video processing\n\n"
        "**Commands:**\n"
        "/start - Initialize bot\n"
        "/help - Show this menu\n"
        "/cancel - Stop current upload and clear queued uploads\n"
        "/status - Check upload status\n"
        "/settings - View preferences\n"
        "/toggle_group - Toggle group upload\n"
        "/set_group_size [1-10] - Set group size\n"
        "/uptime - Check bot uptime\n\n"
        "**Tips:**\n"
        "• Use /cancel to stop long uploads\n"
        "• Photos and videos are grouped together\n"
        "• Documents uploaded separately\n"
        "• Check /status for queue position"
    )
    await event.respond(help_msg)
    logger.info("User requested help.")


@client.on(events.NewMessage(pattern="/uptime"))
async def uptime_command(event):
    """
    Handles the /uptime command.
    """
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    stats_msg = (
        "⏱ **Bot Statistics**\n\n"
        f"**Uptime:** {uptime_str}\n"
        f"**Tasks in Queue:** {task_queue.qsize()}\n"
        f"**Active Uploads:** {len(active_tasks)}\n"
        f"**Active Since:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await event.respond(stats_msg)
    logger.info("User requested uptime.")


@client.on(events.NewMessage(func=lambda e: getattr(e.message, "file", None) and getattr(e.message.file, "name", "").endswith(".zip")))
async def handle_zip(event):
    """
    Adds a new ZIP task to the queue.
    Allows multiple ZIPs per user to be queued.
    """
    user_id = event.chat_id
    file_name = getattr(event.message.file, "name", "<unknown>")

    # Compute existing queued uploads for this user
    overall_queue = list(task_queue._queue)
    existing_user_queue = sum(1 for item in overall_queue if getattr(item[0], "chat_id", None) == user_id)

    overall_position = task_queue.qsize() + 1
    user_position = existing_user_queue + 1
    user_total_after_enqueue = existing_user_queue + 1

    await event.respond(
        f"📥 **ZIP file received:** `{file_name}`\n"
        f"📊 **Global position in queue:** {overall_position}\n"
        f"🧾 **Your uploads in queue (including this):** {user_total_after_enqueue}\n\n"
        f"💡 **Tip:** Use /cancel to stop active upload and clear your queued uploads"
    )

    # enqueue event as a single-item tuple for process_tasks
    await task_queue.put((event,))
    logger.info(f"Queued ZIP file: {file_name} (user: {user_id})")


async def check_cancel_flag(user_id: int):
    """
    Check if the user has requested cancellation.
    """
    if user_id in active_tasks and active_tasks[user_id].get("cancel_flag"):
        raise asyncio.CancelledError("Upload cancelled by user")


async def handle_zip_task(event):
    """
    Processes a ZIP file task with group upload support and cancellation.
    """
    user_id = event.chat_id

    try:
        prefs = user_preferences.get(user_id, {"group_upload": True, "group_size": 10})

        message_file = event.message.file
        file_name = getattr(message_file, "name", "<unknown>")
        file_size = getattr(message_file, "size", 0)

        logger.info(f"Processing ZIP: {file_name} ({file_size / 1024 / 1024:.2f} MB)")

        if file_size > MAX_FILE_SIZE:
            await event.respond(f"❌ **'{file_name}' exceeds the 2 GB file size limit.**")
            return

        os.makedirs("downloads", exist_ok=True)
        zip_path = os.path.join("downloads", file_name)

        # Download the ZIP file
        progress_msg = await event.respond(
            f"📥 **Downloading:** `{file_name}`\n"
            f"📦 **Size:** {file_size / 1024 / 1024:.2f} MB\n\n"
            f"⏳ Please wait... (Use /cancel to stop)"
        )

        # Check for cancellation during download
        await check_cancel_flag(user_id)

        await fast_download(
            client,
            event.message,
            zip_path,
            progress_bar_function=lambda d, t: asyncio.create_task(
                update_progress(progress_msg, d, t, "Downloading", file_name, user_id)
            ),
        )

        # Check for cancellation after download
        await check_cancel_flag(user_id)

        # Validate archive (supports ZIP, RAR, 7z, tar, etc.)
        unzip_dir = os.path.splitext(zip_path)[0]
        os.makedirs(unzip_dir, exist_ok=True)

        # Try to extract using extended extractor
        ok, err = extract_archive_sync(zip_path, unzip_dir, user_id)
        if err == 'NEED_PASSWORD':
            # Archive is encrypted and user password not provided - ask user to supply it
            pending_extractions[user_id] = {'zip_path': zip_path, 'unzip_dir': unzip_dir, 'file_name': file_name, 'progress_msg_id': getattr(progress_msg, 'id', None)}
            await event.respond('🔐 Archive is password-protected. Reply to this message with the password or send /unzip <password>')
            return
        if not ok:
            try:
                await progress_msg.edit(f"{err}")
            except Exception:
                pass
            cleanup(unzip_dir, zip_path)
            return

        # Extraction succeeded — continue to prepare files for upload
# Prepare files for upload
        files_to_upload: List[Dict[str, Any]] = []
        for root, _, files in os.walk(unzip_dir):
            for filename in files:
                await check_cancel_flag(user_id)

                file_path = os.path.join(root, filename)
                f_size = os.path.getsize(file_path)

                if f_size > MAX_FILE_SIZE:
                    await event.respond(f"⚠️ Skipping `{filename}`: Exceeds 2GB limit")
                    continue

                mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                files_to_upload.append(
                    {"path": file_path, "name": filename, "size": f_size, "mime_type": mime_type}
                )

        if not files_to_upload:
            try:
                await progress_msg.edit("❌ **No valid files found in the ZIP.**")
            except Exception:
                pass
            cleanup(unzip_dir, zip_path)
            return

        # Upload files
        if prefs["group_upload"]:
            await upload_files_grouped(event, files_to_upload, progress_msg, file_name, prefs["group_size"], user_id)
        else:
            await upload_files_individual(event, files_to_upload, progress_msg, file_name, user_id)

        cleanup(unzip_dir, zip_path)

        summary_msg = (
            "✅ **Upload Complete!**\n\n"
            f"📁 **Source:** `{file_name}`\n"
            f"📊 **Files Processed:** {len(files_to_upload)}\n"
            f"🎯 **Upload Mode:** {'Mixed Media Groups' if prefs['group_upload'] else 'Individual'}\n\n"
            "Thank you for using the bot! 🚀"
        )
        await event.respond(summary_msg)
        logger.info(f"Completed processing: {file_name}")

    except asyncio.CancelledError:
        # Handle cancellation
        logger.info(f"Upload cancelled for user {user_id}")
        # attempt cleanup
        try:
            if "unzip_dir" in locals() and unzip_dir:
                cleanup(unzip_dir, zip_path if "zip_path" in locals() else None)
        except Exception:
            pass
        raise
    except Exception as e:
        logger.exception(f"Error processing ZIP: {e}")
        try:
            await event.respond(f"❌ **Error:** {str(e)}")
        except Exception:
            pass
        try:
            if "unzip_dir" in locals() and unzip_dir:
                cleanup(unzip_dir, zip_path if "zip_path" in locals() else None)
        except Exception:
            pass


async def upload_files_grouped(
    event,
    files: List[Dict[str, Any]],
    progress_msg,
    zip_name: str,
    group_size: int,
    user_id: int,
):
    """
    Upload files in mixed media groups (photos + videos together).
    Images are treated as photos (uploaded raw) so that albums look clean.
    Videos are uploaded as InputMediaUploadedDocument with DocumentAttributeVideo.
    Documents are uploaded as InputMediaUploadedDocument with filename attribute.

    Album/group uploads will NOT include captions (as requested).
    Single-file sends still include a caption.
    """
    try:
        # Create mixed groups
        groups = create_mixed_media_groups(files, group_size)

        total_uploaded = 0
        total_files = len(files)

        for group_idx, (group_type, group) in enumerate(groups, 1):
            # Check for cancellation
            await check_cancel_flag(user_id)

            media_list: List[Any] = []
            group_file_infos: List[Dict[str, Any]] = []

            # Prepare media for group upload
            for file_info in group:
                # Check for cancellation before each file
                await check_cancel_flag(user_id)

                file_path = file_info["path"]
                mime_type = file_info["mime_type"]

                # IMAGE: treat as photo (raw uploaded file) so it is displayed as photo in albums
                if mime_type.startswith("image/"):
                    width, height = get_image_dimensions(file_path)
                    uploaded_file = await fast_upload(client, file_path)
                    media = uploaded_file

                # VIDEO: keep as uploaded document with video attributes
                elif mime_type.startswith("video/"):
                    add_silent_audio(file_path)
                    metadata = get_video_metadata(file_path)
                    thumb_path = get_video_thumbnail(file_path)

                    uploaded_file = await fast_upload(client, file_path)

                    thumb = None
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            thumb = await fast_upload(client, thumb_path)
                        except Exception:
                            thumb = None
                        try:
                            os.remove(thumb_path)
                        except Exception:
                            pass

                    media = InputMediaUploadedDocument(
                        file=uploaded_file,
                        mime_type=mime_type,
                        attributes=[
                            DocumentAttributeVideo(
                                supports_streaming=True,
                                w=metadata.get("width", 0),
                                h=metadata.get("height", 0),
                                duration=metadata.get("duration", 0),
                            )
                        ],
                        thumb=thumb,
                    )

                # DOCUMENT / OTHER: upload as document with filename attribute
                else:
                    uploaded_file = await fast_upload(client, file_path)
                    media = InputMediaUploadedDocument(
                        file=uploaded_file,
                        mime_type=mime_type,
                        attributes=[DocumentAttributeFilename(file_name=file_info["name"])],
                    )

                media_list.append(media)
                group_file_infos.append(file_info)
                total_uploaded += 1

                # Update progress after preparing this file
                progress_percent = (total_uploaded / total_files) * 100 if total_files else 100
                try:
                    await progress_msg.edit(
                        f"📤 **Uploading Files**\n\n"
                        f"**Type:** {group_type.title()}\n"
                        f"**Group:** {group_idx}/{len(groups)}\n"
                        f"**Progress:** {total_uploaded}/{total_files} files ({progress_percent:.1f}%)\n"
                        f"**Current:** `{file_info['name']}`\n\n"
                        f"🛑 Use /cancel to stop"
                    )
                except Exception:
                    pass

            # After preparing whole group, send it (no caption)
            if media_list:
                logger.info(f"Sending group {group_idx}/{len(groups)} with {len(media_list)} files")
                try:
                    if len(media_list) == 1:
                        # Single file: decide how to send based on type
                        m = media_list[0]
                        file_info = group_file_infos[0]
                        if isinstance(m, InputMediaUploadedDocument):
                            # video or document
                            await client.send_file(
                                event.chat_id,
                                m.file,
                                caption=f"📁 {file_info['name']}",
                                attributes=getattr(m, "attributes", None) or None,
                                thumb=getattr(m, "thumb", None) or None,
                                force_document=False if file_info["mime_type"].startswith("video/") else True,
                            )
                        else:
                            # m is raw uploaded photo -> send as photo (force_document=False)
                            await client.send_file(
                                event.chat_id,
                                m,
                                caption=f"📁 {file_info['name']}",
                                force_document=False,
                            )
                        logger.info(f"Successfully sent single file: {file_info['name']}")
                    else:
                        # Multiple files: attempt to send as album/mixed media WITHOUT caption
                        try:
                            await client.send_file(event.chat_id, media_list)
                            logger.info(f"Successfully sent album with {len(media_list)} files (no caption)")
                        except Exception as album_error:
                            logger.warning(f"Album upload failed, sending individually: {album_error}")
                            # Send files one by one WITHOUT per-file caption for cleanliness
                            for idx, (m, file_info) in enumerate(zip(media_list, group_file_infos), 1):
                                if isinstance(m, InputMediaUploadedDocument):
                                    await client.send_file(
                                        event.chat_id,
                                        m.file,
                                        attributes=getattr(m, "attributes", None) or None,
                                        thumb=getattr(m, "thumb", None) or None,
                                        force_document=False if file_info["mime_type"].startswith("video/") else True,
                                    )
                                else:
                                    await client.send_file(
                                        event.chat_id,
                                        m,
                                        force_document=False,
                                    )
                                logger.info(f"Sent file {idx}/{len(media_list)}: {file_info['name']}")
                                await asyncio.sleep(0.5)
                except Exception as send_error:
                    logger.error(f"Error sending group {group_idx}: {send_error}")
                    try:
                        await event.respond(f"⚠️ Error uploading group {group_idx}: {str(send_error)}")
                    except Exception:
                        pass

            # Small delay between groups to prevent rate limiting
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logger.info(f"Upload cancelled for user {user_id} during grouped upload")
        raise
    except Exception as e:
        logger.error(f"Critical error in upload_files_grouped: {e}")
        raise


async def upload_files_individual(
    event, files: List[Dict[str, Any]], progress_msg, zip_name: str, user_id: int
):
    """
    Upload files individually (legacy mode).
    """
    total_files = len(files)

    for idx, file_info in enumerate(files, 1):
        # Check for cancellation
        await check_cancel_flag(user_id)

        file_path = file_info["path"]
        filename = file_info["name"]
        mime_type = file_info["mime_type"]

        # Process videos
        if mime_type.startswith("video/"):
            add_silent_audio(file_path)
            metadata = get_video_metadata(file_path)
            attributes = [
                DocumentAttributeVideo(
                    supports_streaming=True,
                    w=metadata["width"],
                    h=metadata["height"],
                    duration=metadata["duration"],
                )
            ]
            thumb_path = get_video_thumbnail(file_path)
        else:
            attributes = []
            thumb_path = None

        # Upload file
        uploaded_file = await fast_upload(client, file_path)

        # Upload thumbnail if exists
        thumb = None
        if thumb_path and os.path.exists(thumb_path):
            try:
                thumb = await fast_upload(client, thumb_path)
            except Exception:
                thumb = None
            try:
                os.remove(thumb_path)
            except Exception:
                pass

        # Send file
        await client.send_file(event.chat_id, uploaded_file, caption=f"📄 {filename}", attributes=attributes, thumb=thumb)

        # Update progress
        progress_percent = (idx / total_files) * 100 if total_files else 100
        try:
            await progress_msg.edit(
                f"📤 **Uploading Files (Individual Mode)**\n\n"
                f"**Progress:** {idx}/{total_files} files ({progress_percent:.1f}%)\n"
                f"**Current:** `{filename}`\n\n"
                f"🛑 Use /cancel to stop"
            )
        except Exception:
            pass


async def update_progress(message, current, total, action, filename="", user_id=None):
    """
    Updates progress message with cancellation reminder.
    """
    try:
        # Check if cancelled
        if user_id and user_id in active_tasks and active_tasks[user_id].get("cancel_flag"):
            return

        progress_percent = (current / total) * 100 if total > 0 else 0
        progress_bar = create_progress_bar(progress_percent)

        progress_text = (
            f"📊 **{action}**\n\n"
            f"{progress_bar}\n"
            f"**Progress:** {current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB ({progress_percent:.1f}%)\n"
            f"**File:** `{filename}`\n\n"
            "🛑 Use /cancel to stop"
        )
        await message.edit(progress_text)
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")


def create_progress_bar(percentage: float, length: int = 20) -> str:
    """
    Create a visual progress bar.
    """
    filled = int(length * percentage / 100)
    if filled < 0:
        filled = 0
    if filled > length:
        filled = length
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}]"


def cleanup(unzip_dir: Optional[str], zip_path: Optional[str]):
    """
    Clean up temporary files.
    """
    try:
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)
        if unzip_dir and os.path.exists(unzip_dir):
            shutil.rmtree(unzip_dir, ignore_errors=True)
        logger.info(f"Cleaned up temporary files")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


async def main():
    """
    Main bot loop.
    """
    # Start task processor
    asyncio.create_task(process_tasks())

    while True:
        try:
            await client.start(bot_token=BOT_TOKEN)
            logger.info("🚀 Bot started successfully!")
            print("🚀 Enhanced ZIP Uploader Bot is running...")
            print("🎬 Mixed media groups enabled (photos + videos)")
            print("🛑 Cancel command available")
            print("⚡ Ready to process ZIP files!")
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Bot disconnected: {e}")
            print("❌ Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
