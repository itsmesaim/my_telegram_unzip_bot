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
from FastTelethonhelper import fast_download, fast_upload  # Import FastTelethon functions
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
    await event.respond("Welcome! Send me a ZIP file, and I will upload all files using Telethon.")
    logger.info("User started the bot.")

@client.on(events.NewMessage(pattern='/help'))
async def help_command(event):
    await event.respond("Send me a ZIP file, and I will process its contents.")
    logger.info("User requested help.")

@client.on(events.NewMessage(pattern='/uptime'))
async def uptime_command(event):
    uptime = datetime.now() - start_time
    uptime_str = str(timedelta(seconds=uptime.total_seconds()))
    await event.respond(f"Bot Uptime: {uptime_str}")
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
            await event.respond("Please upload a valid ZIP file.")
            return
        
        file_name = message_file.name
        file_size = message_file.size

        if file_size > MAX_FILE_SIZE:
            await event.respond(f"'{file_name}' is too large (max 2 GB).")
            return

        os.makedirs('downloads', exist_ok=True)
        zip_path = os.path.join('downloads', file_name)

        progress_msg = await event.respond(f"Downloading ZIP file: {file_name}...")
        await fast_download(client, event.message, zip_path, progress_bar_function=lambda d, t: asyncio.create_task(
            update_progress(progress_msg, d, t, "Downloading...")))

        unzip_dir = zip_path.rstrip('.zip')
        os.makedirs(unzip_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)

        upload_tasks = []
        for root, _, files in os.walk(unzip_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                if os.path.getsize(file_path) > MAX_FILE_SIZE:
                    await event.respond(f"Skipping '{filename}': File size exceeds 2 GB.")
                    continue
                upload_tasks.append(upload_file(event, file_path))

        await asyncio.gather(*upload_tasks)

        cleanup(unzip_dir, zip_path)
        await event.respond(f"All files from {file_name} have been uploaded successfully.")

    except Exception as e:
        logger.exception(f"Error processing ZIP file: {e}")
        await event.respond(f"An error occurred: {e}")

async def upload_file(event, file_path):
    try:
        file_name = os.path.basename(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        is_video = mime_type and mime_type.startswith('video/')

        if is_video:
            metadata = get_video_metadata(file_path)
            width, height, duration = metadata['width'], metadata['height'], metadata['duration']

            if width == 0 or height == 0:
                await event.respond(f"Skipping {file_name}: Unable to fetch video metadata.")
                return

            add_silent_audio(file_path)

            attributes = [
                DocumentAttributeVideo(
                    supports_streaming=True,
                    w=width,
                    h=height,
                    duration=duration
                )
            ]
            progress_msg = await event.respond(f"Uploading video: {file_name}...")
            await fast_upload(client, file_path, progress_bar_function=lambda d, t: asyncio.create_task(
                update_progress(progress_msg, d, t, "Uploading...")))
            await client.send_file(
                event.chat_id,
                file_path,
                caption=f"Video: {file_name}",
                attributes=attributes
            )
        else:
            progress_msg = await event.respond(f"Uploading file: {file_name}...")
            await fast_upload(client, file_path, progress_bar_function=lambda d, t: asyncio.create_task(
                update_progress(progress_msg, d, t, "Uploading...")))
            await client.send_file(
                event.chat_id,
                file_path,
                caption=f"File: {file_name}"
            )
    except Exception as e:
        logger.exception(f"Failed to upload {file_path}: {e}")
        await event.respond(f"Failed to upload {file_name}: {e}")

async def update_progress(message, downloaded, total, action):
    try:
        percent = (downloaded / total) * 100
        await message.edit(f"{action} {percent:.2f}% complete")
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")

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
