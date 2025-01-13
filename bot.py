import os
import zipfile
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from dotenv import load_dotenv
from telethon import TelegramClient

# Load environment variables from .env file
load_dotenv()

# Telegram Bot Token and API credentials
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')

# Initialize Telethon Client (for faster uploads)
client = TelegramClient('bot_session', API_ID, API_HASH)

# Initialize logging
logging.basicConfig(
    filename='logs/bot.log',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Command: /start
async def start(update: Update, context: CallbackContext):
    logger.info("User started the bot.")
    await update.message.reply_text("Welcome! Send me a ZIP file, and I will upload photos, videos, and unknown files as documents.")

# Command: /help
async def help_command(update: Update, context: CallbackContext):
    logger.info("User requested help.")
    await update.message.reply_text("Send me a ZIP file with photos, videos, and I will upload files accordingly. Unknown files will be uploaded as documents.")

# Report progress to the user based on log contents
async def report_progress(update: Update):
    log_file = 'logs/bot.log'
    if os.path.exists(log_file):
        with open(log_file, 'r') as file:
            lines = file.readlines()
            recent_logs = '\n'.join(lines[-10:])  # Fetch last 10 log lines for brevity
            await update.message.reply_text(f"Recent Progress Logs:\n\n{recent_logs}")
    else:
        await update.message.reply_text("No logs found. Processing might not have started yet.")

# Handle ZIP file upload
async def handle_zip(update: Update, context: CallbackContext):
    logger.info("Received a ZIP file.")
    try:
        # Ensure it's a ZIP file
        if update.message.document and update.message.document.mime_type == 'application/zip':
            file = await update.message.document.get_file()
            file_name = update.message.document.file_name

            logger.info(f"Processing ZIP file: {file_name}")

            # Create directory to store the zip file and its contents
            if not os.path.exists('downloads'):
                os.makedirs('downloads')

            zip_path = os.path.join('downloads', file_name)

            # Download ZIP file
            logger.info(f"Downloading ZIP file: {file_name}")
            await file.download_to_drive(zip_path)

            # Notify the user that the download is complete
            await update.message.reply_text(f"Downloaded ZIP file: {file_name}. Starting to process...")

            # Unzip the file
            unzip_dir = os.path.join('downloads', file_name.replace('.zip', ''))
            os.makedirs(unzip_dir, exist_ok=True)

            logger.info(f"Unzipping file: {file_name}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(unzip_dir)

            # Classify and upload each file
            for root, dirs, files in os.walk(unzip_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    mime_type, _ = mimetypes.guess_type(file_path)

                    if mime_type:
                        logger.info(f"Processing file: {filename} | MIME type: {mime_type}")
                        if mime_type.startswith('image/'):
                            await send_photo_or_video(update, file_path, is_image=True)
                        elif mime_type.startswith('video/'):
                            await send_photo_or_video(update, file_path, is_image=False)
                        else:
                            # Upload unknown formats as documents
                            await send_document(file_path, update)
                    else:
                        # Upload unknown formats as documents
                        logger.info(f"Unknown file format for: {filename}. Uploading as document.")
                        await send_document(file_path, update)

            # Clean up: Delete downloaded and extracted files
            cleanup(unzip_dir, zip_path)
            logger.info(f"Cleaned up files for: {file_name}")
            await update.message.reply_text(f"Successfully uploaded files from {file_name}")

            # Send final log report to the user
            await report_progress(update)
        else:
            logger.error("Received file is not a ZIP file.")
            await update.message.reply_text("Please upload a valid ZIP file.")
    except Exception as e:
        logger.error(f"Error processing ZIP file: {e}")
        await update.message.reply_text(f"An error occurred: {e}")

# Function to upload photo or video using Telethon
async def send_photo_or_video(update: Update, file_path: str, is_image: bool):
    try:
        # Use Telethon for faster upload
        chat_id = update.message.chat_id
        if is_image:
            logger.info(f"Uploading photo: {os.path.basename(file_path)}")
        else:
            logger.info(f"Uploading video: {os.path.basename(file_path)}")

        # Send file using Telethon
        await client.send_file(chat_id, file_path)
    except Exception as e:
        logger.error(f"Error uploading {'photo' if is_image else 'video'} {file_path}: {e}")

# Function to upload unknown files as documents
async def send_document(file_path: str, update: Update):
    try:
        chat_id = update.message.chat_id
        logger.info(f"Uploading unknown format file as document: {os.path.basename(file_path)}")

        # Send document using Telethon
        await client.send_file(chat_id, file_path)
    except Exception as e:
        logger.error(f"Error uploading document {file_path}: {e}")

# Cleanup function
def cleanup(unzip_dir, zip_path):
    try:
        # Remove all unzipped files and the zip file itself
        os.remove(zip_path)
        for root, dirs, files in os.walk(unzip_dir):
            for file in files:
                os.remove(os.path.join(root, file))
        os.rmdir(unzip_dir)
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

# Error handler
async def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Update {update} caused error: {context.error}")

def main():
    # Initialize bot application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Connect to Telegram API with Telethon (for faster uploads)
    client.start()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Handle zip file uploads
    application.add_handler(MessageHandler(filters.Document.MimeType("application/zip"), handle_zip))

    # Log all errors
    application.add_error_handler(error_handler)

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()
