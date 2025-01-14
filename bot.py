import os
import zipfile
import logging
import mimetypes
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Use StringSession for session management
SESSION_STRING = os.getenv('SESSION_STRING')  # Save this in .env for persistence
if not SESSION_STRING:
    SESSION_STRING = None  # First-time setup

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

# Function to handle /start command
async def start_command(event):
    await event.respond("Welcome! Send me a ZIP file, and I will upload all files using Telethon.")
    logger.info("User started the bot.")

# Function to handle /help command
async def help_command(event):
    await event.respond("Send me a ZIP file, and I will process its contents. Images and videos will be sent accordingly, and other files will be sent as documents.")
    logger.info("User requested help.")

# Function to handle /uptime command
async def uptime_command(event):
    uptime = datetime.now() - start_time
    uptime_str = str(timedelta(seconds=uptime.total_seconds()))
    await event.respond(f"Bot Uptime: {uptime_str}")
    logger.info("User requested uptime.")

# Function to process ZIP files
async def handle_zip(event):
    try:
        if event.message.file and event.message.file.name.endswith('.zip'):
            file_name = event.message.file.name
            file_size = event.message.file.size

            # Check the file size before proceeding
            if file_size > MAX_FILE_SIZE:
                await event.respond(f"File '{file_name}' is too large (exceeds 2 GB). Please upload a smaller file.")
                logger.warning(f"File '{file_name}' is too large. Skipping processing.")
                return

            zip_path = os.path.join('downloads', file_name)
            os.makedirs('downloads', exist_ok=True)

            # Notify user about downloading
            await event.respond(f"Downloading ZIP file: {file_name}...")
            logger.info(f"Downloading ZIP file: {file_name}")

            # Download the ZIP file
            await event.download_media(file=zip_path)
            await event.respond(f"Downloaded ZIP file: {file_name}. Starting extraction...")

            # Unzip the file
            unzip_dir = os.path.join('downloads', file_name.replace('.zip', ''))
            os.makedirs(unzip_dir, exist_ok=True)

            logger.info(f"Extracting file: {file_name}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(unzip_dir)
            await event.respond(f"Extraction complete. Preparing to upload files...")

            # Process each file
            for root, dirs, files in os.walk(unzip_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    mime_type, _ = mimetypes.guess_type(file_path)

                    # Check if the extracted file size exceeds 2 GB
                    if os.path.getsize(file_path) > MAX_FILE_SIZE:
                        logger.warning(f"File '{filename}' is too large (exceeds 2 GB). Skipping.")
                        await event.respond(f"Skipping '{filename}': File size exceeds 2 GB.")
                        continue

                    if mime_type:
                        logger.info(f"Processing file: {filename} | MIME type: {mime_type}")
                        if mime_type.startswith('image/'):
                            await send_photo_or_video(event, file_path, is_image=True)
                        elif mime_type.startswith('video/'):
                            await send_photo_or_video(event, file_path, is_image=False)
                        else:
                            await send_document(event, file_path)
                    else:
                        logger.info(f"Unknown file format for: {filename}. Uploading as document.")
                        await send_document(event, file_path)

            cleanup(unzip_dir, zip_path)
            logger.info(f"Cleaned up files for: {file_name}")
            await event.respond(f"All files from {file_name} have been uploaded successfully.")

        else:
            logger.error("Received file is not a ZIP file.")
            await event.respond("Please upload a valid ZIP file.")
    except Exception as e:
        logger.error(f"Error processing ZIP file: {e}")
        await event.respond(f"An unexpected error occurred. Please try again later.")

# Function to send photos or videos
async def send_photo_or_video(event, file_path: str, is_image: bool):
    try:
        file_name = os.path.basename(file_path)

        if is_image:
            logger.info(f"Uploading photo: {file_name}")
            await event.respond(f"Uploading photo: {file_name}...")
        else:
            logger.info(f"Uploading video: {file_name}")
            await event.respond(f"Uploading video: {file_name}...")

        await client.send_file(
            event.chat_id,
            file_path,
            caption=f"Uploading {'photo' if is_image else 'video'}: {file_name}",
            attributes=[DocumentAttributeFilename(file_name)]
        )
    except Exception as e:
        logger.error(f"Error uploading {'photo' if is_image else 'video'} {file_path}: {e}")
        await event.respond(f"Failed to upload {'photo' if is_image else 'video'}: {file_name}.")

# Function to send documents
async def send_document(event, file_path: str):
    try:
        file_name = os.path.basename(file_path)
        logger.info(f"Uploading document: {file_name}")
        await event.respond(f"Uploading document: {file_name}...")

        await client.send_file(
            event.chat_id,
            file_path,
            caption=f"Uploading document: {file_name}"
        )
    except Exception as e:
        logger.error(f"Error uploading document {file_path}: {e}")
        await event.respond(f"Failed to upload document: {file_name}.")

# Cleanup function
def cleanup(unzip_dir, zip_path):
    try:
        os.remove(zip_path)
        for root, dirs, files in os.walk(unzip_dir):
            for file in files:
                os.remove(os.path.join(root, file))
        os.rmdir(unzip_dir)
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

# Main function
async def main():
    # Start Telethon client
    await client.start(bot_token=BOT_TOKEN)

    # Save session string if first-time setup
    if not SESSION_STRING:
        session_string = StringSession.save(client.session)
        print("Session string:", session_string)
        print("Save this session string in your environment variables for future use.")

    # Register command handlers
    @client.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        await start_command(event)

    @client.on(events.NewMessage(pattern='/help'))
    async def help_handler(event):
        await help_command(event)

    @client.on(events.NewMessage(pattern='/uptime'))
    async def uptime_handler(event):
        await uptime_command(event)

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.message.file and e.message.file.name.endswith('.zip')))
    async def zip_handler(event):
        await handle_zip(event)

    logger.info("Bot is running...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
