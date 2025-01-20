# bot/main.py
import os
import zipfile
import shutil
import logging
import mimetypes
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
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
    filename='logs/bot.log',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("BotLogger")

# Constants
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB limit for Telethon
start_time = datetime.now()

@client.on(events.NewMessage(pattern='/start'))
async def start_command(event):
    await event.respond("👋 **Welcome!**\n\nSend me a ZIP file, and I will upload its contents to this chat.")
    logger.info("User started the bot.")

@client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    await event.respond("📜 **Help**\n\nSend me a ZIP file, and I will process its contents.\n\n**Commands:**\n/start - Start the bot\n/help - Show this help message\n/uptime - Check bot uptime")
    logger.info("User requested help.")

@client.on(events.NewMessage(pattern='/uptime'))
async def uptime_command(event):
    uptime = datetime.now() - start_time
    uptime_str = str(timedelta(seconds=uptime.total_seconds()))
    await event.respond(f"⏱ **Bot Uptime:** {uptime_str}")
    logger.info("User requested uptime.")

def get_video_metadata(file_path):
    try:
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
            'stream=width,height,duration', '-of', 'json', file_path
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
    try:
        temp_path = f"{file_path}_temp.mp4"
        cmd = [
            'ffmpeg', '-i', file_path, '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-shortest', '-c:v', 'copy', '-c:a', 'aac', temp_path
        ]
        subprocess.run(cmd, check=True)
        os.replace(temp_path, file_path)
    except Exception as e:
        logger.error(f"Failed to add silent audio to {file_path}: {e}")

@client.on(events.NewMessage(func=lambda e: e.message.file and e.message.file.name.endswith('.zip')))
async def handle_zip(event):
    try:
        message_file = event.message.file
        if not (message_file and message_file.name.endswith('.zip')):
            await event.respond("❌ **Invalid file type. Please upload a ZIP file.**")
            return

        file_name = message_file.name
        file_size = message_file.size

        if file_size > MAX_FILE_SIZE:
            await event.respond(f"❌ **'{file_name}' exceeds the 2 GB file size limit.**")
            return

        os.makedirs('downloads', exist_ok=True)
        zip_path = os.path.join('downloads', file_name)

        progress_msg = await event.respond(f"📥 **Downloading ZIP file:** `{file_name}`\n\n**Please wait…**")
        await fast_download(client, event.message, zip_path, progress_bar_function=lambda d, t: asyncio.create_task(
            update_progress(progress_msg, d, t, "Downloading ZIP")))

        unzip_dir = zip_path.rstrip('.zip')
        os.makedirs(unzip_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)

        # Unified progress message for uploads
        upload_progress_msg = await event.respond(f"📤 **Uploading files from ZIP:** `{file_name}`\n\n**Please wait…**")
        total_files = sum(len(files) for _, _, files in os.walk(unzip_dir))
        uploaded_files = 0

        for root, _, files in os.walk(unzip_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                if os.path.getsize(file_path) > MAX_FILE_SIZE:
                    await event.respond(f"⚠️ Skipping `{filename}`: File size exceeds 2 GB.")
                    continue

                # Perform the upload
                uploaded_path = await fast_upload(client, file_path)
                await client.send_file(event.chat_id, file_path, caption=f"📄 **File Uploaded:** `{filename}`")
                uploaded_files += 1

                # Update progress message
                await update_progress_files(upload_progress_msg, uploaded_files, total_files)

        cleanup(unzip_dir, zip_path)
        await upload_progress_msg.edit(f"✅ **All files from `{file_name}` have been uploaded successfully.**")

    except Exception as e:
        logger.exception(f"Error processing ZIP file: {e}")
        await event.respond(f"❌ **An error occurred:** {e}")

async def update_progress(message, downloaded, total, action):
    try:
        percent = (downloaded / total) * 100
        progress_bar = "█" * int(percent // 5) + "░" * (20 - int(percent // 5))
        progress_text = f"""
**{action} Progress**
[{progress_bar}] {percent:.2f}%

**Downloaded:** {downloaded / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB
"""
        await message.edit(progress_text)
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")

async def update_progress_files(message, uploaded, total):
    try:
        percent = (uploaded / total) * 100
        progress_bar = "█" * int(percent // 5) + "░" * (20 - int(percent // 5))
        progress_text = f"""
**Uploading Progress**
[{progress_bar}] {percent:.2f}%

**Uploaded Files:** {uploaded} / {total}
"""
        await message.edit(progress_text)
    except Exception as e:
        logger.error(f"Failed to update file upload progress: {e}")

def cleanup(unzip_dir, zip_path):
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.rmtree(unzip_dir, ignore_errors=True)
    except Exception as e:
        logger.exception(f"Cleanup error: {e}")

async def main():
    await client.start(bot_token=BOT_TOKEN)

    if not os.path.exists(SESSION_FILE):
        session_string = StringSession.save(client.session)
        with open(SESSION_FILE, 'w') as session_file:
            session_file.write(session_string)
        print("Session string saved to 'session_save.txt'")

    logger.info("Bot is running...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
