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
from telethon.tl.types import InputFile, DocumentAttributeVideo
from FastTelethonhelper import fast_download, fast_upload
import subprocess
import json
import asyncio

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Load session string from file if available
SESSION_FILE = 'session_save.txt'
if os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, 'r') as file:
        SESSION_STRING = file.read().strip()
else:
    SESSION_STRING = ""

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Initialize logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to see detailed logs in the terminal
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger("BotLogger")

# Constants
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB limit for Telethon
start_time = datetime.now()


def get_video_metadata(file_path):
    """
    Extract metadata from a video file using FFmpeg.
    """
    try:
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,duration',
            '-of', 'json', file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        metadata = json.loads(result.stdout)
        stream = metadata.get('streams', [{}])[0]
        return {
            'width': int(stream.get('width', 0)),
            'height': int(stream.get('height', 0)),
            'duration': int(float(stream.get('duration', 0)))
        }
    except Exception as e:
        logger.error(f"Failed to extract metadata for {file_path}: {e}")
        return {'width': 0, 'height': 0, 'duration': 0}


def add_silent_audio(file_path):
    """
    Add silent audio to a video file to prevent it from being uploaded as a GIF.
    """
    try:
        temp_path = f"{file_path}_temp.mp4"
        cmd = [
            'ffmpeg', '-i', file_path, '-f', 'lavfi',
            '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-shortest', '-c:v', 'copy', '-c:a', 'aac', temp_path
        ]
        subprocess.run(cmd, check=True)
        os.replace(temp_path, file_path)
        print(f"✅ Added silent audio to {file_path}")
    except Exception as e:
        logger.error(f"Failed to add silent audio to {file_path}: {e}")


@client.on(events.NewMessage(pattern='/start'))
async def start_command(event):
    """
    Handles the /start command.
    """
    print(f"📩 Received /start from {event.chat_id}")
    await event.respond("👋 Hello! Send me a ZIP file, and I will upload its contents to this chat.")
    logger.info("User started the bot.")


@client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    """
    Handles the /help command.
    """
    print(f"📩 Received /help from {event.chat_id}")
    await event.respond("📜 **Help**\n\nSend me a ZIP file, and I will process its contents.")
    logger.info("User requested help.")


@client.on(events.NewMessage(pattern='/uptime'))
async def uptime_command(event):
    """
    Handles the /uptime command.
    """
    uptime = datetime.now() - start_time
    uptime_str = str(timedelta(seconds=uptime.total_seconds()))
    print(f"📩 Received /uptime from {event.chat_id}")
    await event.respond(f"⏱ **Bot Uptime:** {uptime_str}")
    logger.info("User requested uptime.")


@client.on(events.NewMessage(func=lambda e: e.message.file and e.message.file.name.endswith('.zip')))
async def handle_zip(event):
    """
    Handles ZIP file uploads.
    """
    try:
        message_file = event.message.file
        file_name = message_file.name
        file_size = message_file.size
        print(f"📂 Received ZIP file: {file_name} ({file_size / 1024 / 1024:.2f} MB)")

        if file_size > MAX_FILE_SIZE:
            await event.respond(f"❌ **'{file_name}' exceeds the 2 GB file size limit.**")
            return

        os.makedirs('downloads', exist_ok=True)
        zip_path = os.path.join('downloads', file_name)

        # Download the ZIP file
        progress_msg = await event.respond(f"📥 **Downloading ZIP file:** `{file_name}`\n\n**Please wait…**")
        print(f"⬇️ Starting download for {file_name}")
        await fast_download(client, event.message, zip_path, progress_bar_function=lambda d, t: asyncio.create_task(
            update_progress(progress_msg, d, t, "Downloading ZIP")
        ))
        print(f"✅ Download complete for {file_name}")

        # Validate if the file is a valid ZIP
        if not zipfile.is_zipfile(zip_path):
            await progress_msg.edit("❌ **The uploaded file is not a valid ZIP archive.**")
            os.remove(zip_path)
            return

        # Extract the ZIP file
        unzip_dir = zip_path.rstrip('.zip')
        os.makedirs(unzip_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)
        print(f"✅ Extracted ZIP file: {file_name}")

        # Unified progress message for uploads
        upload_progress_msg = await event.respond(f"📤 **Uploading files from ZIP:** `{file_name}`\n\n**Please wait…**")
        total_size = sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(unzip_dir) for f in files)
        total_files = sum(len(files) for _, _, files in os.walk(unzip_dir))
        uploaded_size = 0
        uploaded_files = 0

        for root, _, files in os.walk(unzip_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                file_size = os.path.getsize(file_path)

                if file_size > MAX_FILE_SIZE:
                    await event.respond(f"⚠️ Skipping `{filename}`: File size exceeds 2 GB.")
                    print(f"⚠️ Skipped {filename} (File size too large)")
                    continue

                mime_type = mimetypes.guess_type(file_path)[0]
                is_video = mime_type and mime_type.startswith('video/')

                # Add silent audio if the file is a video
                if is_video:
                    add_silent_audio(file_path)
                    metadata = get_video_metadata(file_path)
                    attributes = [
                        DocumentAttributeVideo(
                            supports_streaming=True,
                            w=metadata['width'],
                            h=metadata['height'],
                            duration=metadata['duration']
                        )
                    ]
                else:
                    attributes = []

                # Upload the file using fast upload
                uploaded_file = await fast_upload(client, file_path)
                await client.send_file(
                    event.chat_id,
                    uploaded_file,
                    caption=f"📄 **File Uploaded:** `{filename}`",
                    attributes=attributes
                )
                uploaded_size += file_size
                uploaded_files += 1
                print(f"✅ Uploaded {filename} ({file_size / 1024 / 1024:.2f} MB)")

                # Update upload progress
                await update_upload_progress(upload_progress_msg, uploaded_size, total_size, uploaded_files, total_files)

        cleanup(unzip_dir, zip_path)
        await upload_progress_msg.edit(f"✅ **All files from `{file_name}` have been uploaded successfully.**")

    except Exception as e:
        logger.exception(f"Error processing ZIP file: {e}")
        print(f"❌ An error occurred: {e}")
        await event.respond(f"❌ **An error occurred:** {e}")


async def update_progress(message, current, total, action):
    """
    Updates the download progress.
    """
    try:
        progress_text = f"""
**{action} Progress**

**Completed:** {current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB
"""
        print(f"🔄 {action} Progress: {current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB")
        await message.edit(progress_text)
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")


async def update_upload_progress(message, uploaded_size, total_size, uploaded_files, total_files):
    """
    Updates the upload progress.
    """
    try:
        progress_text = f"""
**Uploading Progress**

**Uploaded:** {uploaded_size / 1024 / 1024:.2f} MB / {total_size / 1024 / 1024:.2f} MB  
**Files Uploaded:** {uploaded_files} / {total_files}
"""
        print(f"🔄 Upload Progress: {uploaded_size / 1024 / 1024:.2f} MB / {total_size / 1024 / 1024:.2f} MB, {uploaded_files}/{total_files} files")
        await message.edit(progress_text)
    except Exception as e:
        logger.error(f"Failed to update upload progress: {e}")


def cleanup(unzip_dir, zip_path):
    """
    Cleans up the extracted files and downloaded ZIP.
    """
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.rmtree(unzip_dir, ignore_errors=True)
        print(f"🧹 Cleaned up {zip_path} and extracted files.")
    except Exception as e:
        logger.exception(f"Cleanup error: {e}")


async def main():
    """
    Starts the bot and handles reconnections.
    """
    while True:
        try:
            await client.start(bot_token=BOT_TOKEN)
            print("🚀 Bot is running...")
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Disconnected! Reconnecting in 5 seconds: {e}")
            print(f"❌ Disconnected! Reconnecting in 5 seconds: {e}")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
