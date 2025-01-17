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
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
import subprocess
import json
import asyncio

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Use StringSession for session management
SESSION_STRING = os.getenv('SESSION_STRING')
client = TelegramClient(StringSession(SESSION_STRING or ""), API_ID, API_HASH)

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

# Function to handle /start command
async def start_command(event):
    await event.respond("Welcome! Send me a ZIP file, and I will upload all files using Telethon.")
    logger.info("User started the bot.")

# Function to handle /help command
async def help_command(event):
    await event.respond("Send me a ZIP file, and I will process its contents.")
    logger.info("User requested help.")

# Function to handle /uptime command
async def uptime_command(event):
    uptime = datetime.now() - start_time
    uptime_str = str(timedelta(seconds=uptime.total_seconds()))
    await event.respond(f"Bot Uptime: {uptime_str}")
    logger.info("User requested uptime.")

# Extract video metadata using ffmpeg
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

# Process ZIP files with optimized video uploads
async def handle_zip(event):
    try:
        message_file = event.message.file
        if not (message_file and message_file.name.endswith('.zip')):
            await event.respond("Please upload a valid ZIP file.")
            return
        
        file_name = message_file.name
        file_size = message_file.size

        # Check size limit
        if file_size > MAX_FILE_SIZE:
            await event.respond(f"'{file_name}' is too large (max 2 GB).")
            return

        os.makedirs('downloads', exist_ok=True)
        zip_path = os.path.join('downloads', file_name)

        await event.respond(f"Downloading ZIP file: {file_name}...")
        await event.download_media(file=zip_path)

        unzip_dir = zip_path.rstrip('.zip')
        os.makedirs(unzip_dir, exist_ok=True)

        # Extract files
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)

        # Use asyncio for concurrent uploads
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
            # Get video metadata
            metadata = get_video_metadata(file_path)
            width, height, duration = metadata['width'], metadata['height'], metadata['duration']

            # Ensure valid metadata for upload
            if width == 0 or height == 0:
                await event.respond(f"Skipping {file_name}: Unable to fetch video metadata.")
                return

            attributes = [
                DocumentAttributeVideo(
                    supports_streaming=True,
                    w=width,
                    h=height,
                    duration=duration
                )
            ]
            await client.send_file(
                event.chat_id,
                file_path,
                caption=f"Uploading video: {file_name}",
                attributes=attributes
            )
        else:
            await client.send_file(
                event.chat_id,
                file_path,
                attributes=[DocumentAttributeFilename(file_name)],
                caption=f"Uploading file: {file_name}"
            )

        logger.info(f"Uploaded: {file_name}")

    except Exception as e:
        logger.exception(f"Failed to upload {file_path}: {e}")
        await event.respond(f"Failed to upload {file_name}: {e}")

def cleanup(unzip_dir, zip_path):
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.rmtree(unzip_dir, ignore_errors=True)
    except Exception as e:
        logger.exception(f"Cleanup error: {e}")

# Main function
async def main():
    await client.start(bot_token=BOT_TOKEN)
    if not SESSION_STRING:
        print("Session string:", StringSession.save(client.session))
    client.add_event_handler(start_command, events.NewMessage(pattern='/start'))
    client.add_event_handler(help_command, events.NewMessage(pattern='/help'))
    client.add_event_handler(uptime_command, events.NewMessage(pattern='/uptime'))
    client.add_event_handler(handle_zip, events.NewMessage(func=lambda e: e.message.file and e.message.file.name.endswith('.zip')))
    logger.info("Bot is running...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
