# 📦 Premium Telegram Unzip Bot

A powerful Telegram bot that extracts and uploads files from compressed archives with advanced features like queue management, password protection, and automatic video optimization.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ✨ Features

### Core Features
- 📦 **Multi-Format Support**: ZIP, RAR, 7Z, TAR, GZ, TGZ, BZ2
- 🔐 **Password Protection**: Supports encrypted archives
- 📊 **Smart Queue System**: Processes one archive at a time, queues others
- 🎬 **Video Optimization**: Automatically adds silent audio track to muted videos (prevents GIF conversion)
- 🖼️ **Media Albums**: Groups images and videos in albums (up to 10 per group)
- 📄 **Document Support**: PDF, DOCX, XLSX, PPTX, TXT, HTML, EPUB, and more
- ⚡ **Premium Speed**: Uses FastTelethon for faster uploads/downloads
- 🛑 **Real-time Cancellation**: Cancel downloads/uploads instantly

### Advanced Features
- 👤 **Multi-User Support**: Each user gets their own queue
- 📊 **Live Progress**: Real-time progress bars with cancel buttons
- 🔄 **Auto-Retry**: Continues on single file failures
- 📝 **Detailed Logs**: Daily log files for debugging
- 💾 **Smart Cleanup**: Automatic temporary file cleanup
- 🎯 **File Filtering**: Skips files over 2GB (Telegram limit)

## 📋 Requirements

- Python 3.10 or higher
- FFmpeg (for video processing)
- Telegram Bot Token
- Telegram API credentials (API_ID & API_HASH)

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/Telegram-Unzip-Bot.git
cd Telegram-Unzip-Bot
```

### 2. Install FFmpeg

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg -y
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

### 3. Install Python Dependencies
```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# OR
.venv\Scripts\activate  # Windows

# Install requirements
python3 -m pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```env
# Get from https://my.telegram.org/apps
API_ID=12345678
API_HASH=your_api_hash_here

# Get from @BotFather on Telegram
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Optional: For premium features (leave empty if not using)
SESSION_STRING=
```

#### How to Get Credentials:

**API_ID & API_HASH:**
1. Go to https://my.telegram.org/apps
2. Login with your Telegram account
3. Create a new application
4. Copy `api_id` and `api_hash`

**BOT_TOKEN:**
1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions
3. Copy the bot token provided

**SESSION_STRING (Optional):**
- Only needed for premium Telegram features
- Generate using [Telethon Session Generator](https://github.com/termux-user/session-generator)

### 5. Run the Bot
```bash
python3 bot.py
```

You should see:
```
════════════════════════════════════
 PREMIUM UNZIP BOT STARTED - @hellopeter3
════════════════════════════════════
BOT BY @hellopeter3 IS 100% READY & ONLINE!
```

## 📖 Usage Guide

### Basic Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and see welcome message |
| `/status` | View current queue and your position |
| `/cancel` | Cancel your current or queued task |
| `/pass <password>` | Set password for encrypted archives |
| `/uptime` | Check bot uptime |

### Button Commands

- **📊 Status** - Check queue status
- **ℹ️ Help** - Show help message
- **⏱ Uptime** - View bot uptime
- **❌ Cancel** - Cancel current operation

### How to Use

#### 1. Extract Simple Archive
1. Send any supported archive file to the bot
2. Wait for extraction and upload
3. Receive all files organized in albums

#### 2. Extract Password-Protected Archive
1. Send `/pass yourpassword`
2. Send the encrypted archive
3. Bot will extract using the provided password

#### 3. Multiple Archives (Queue)
1. Send multiple archives
2. Bot processes one at a time
3. Use `/status` to check your position
4. Use `/cancel` to remove from queue

#### 4. Cancel Operation
During download/upload:
- Click **❌ Cancel** button in status message
- OR send `/cancel` command
- Operation stops immediately

## 🎯 Supported Formats

### Archive Formats
- `.zip` (including password-protected)
- `.rar` (including password-protected)
- `.7z` (including password-protected)
- `.tar`
- `.gz` / `.tgz`
- `.bz2`

### File Types Recognized
- **Images**: JPG, PNG, GIF, WebP, BMP, SVG
- **Videos**: MP4, AVI, MKV, MOV, WMV, FLV, WebM
- **Documents**: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX
- **Text**: TXT, LOG, CSV, MD, HTML, HTM
- **Books**: EPUB
- **Others**: All files supported (sent as documents)

## ⚙️ Configuration

### File Size Limits
- Maximum file size: **2GB** (Telegram's limit)
- Files larger than 2GB are automatically skipped

### Video Processing
- Automatically detects muted videos
- Adds silent audio track to prevent GIF conversion
- Preserves video quality

### Queue Settings
- One archive processed at a time per bot instance
- Multiple users can queue simultaneously
- Each user can have one active task

## 🔧 Advanced Configuration

### Custom Logging
Logs are stored in `logs/` directory with daily rotation:
```
logs/bot_2025-11-30.log
```

### Modify Queue Behavior
Edit `bot.py` to change queue limits:
```python
# Allow multiple files per user (not recommended)
if user_id in user_in_queue:
    # Remove this check to allow multiple queues
    pass
```

### Change Upload Settings
```python
# Modify media group size (default: 10)
if len(media_group) == 10:  # Change to 5 or other number
    await client.send_file(event.chat_id, media_group)
```

## 🐛 Troubleshooting

### Bot Not Starting
```bash
# Check Python version
python3 --version  # Should be 3.10+

# Check if virtual environment is activated
which python  # Should show .venv path

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### FFmpeg Not Found
```bash
# Verify FFmpeg installation
ffmpeg -version

# If not found, install:
sudo apt install ffmpeg  # Linux
brew install ffmpeg      # macOS
```

### Upload Errors
- **Check file size**: Files over 2GB are not supported
- **Check format**: Ensure archive is not corrupted
- **Check network**: Stable internet required for large uploads

### Password Issues
- Password must be sent **before** the archive
- Use `/pass password` (no quotes)
- Password is cleared after successful extraction

### Cancel Not Working
- Ensure you're using the latest code version
- Cancel works during download/upload, not extraction
- Check logs for error messages

## 📊 Performance Tips

1. **Compress archives properly**: Use standard compression for faster extraction
2. **Smaller files**: Break large archives into multiple smaller ones
3. **Network**: Use stable, high-speed internet connection
4. **Server**: Host on VPS for 24/7 availability

## 🔒 Security Notes

- Passwords are stored temporarily in memory only
- Passwords are cleared after extraction
- Files are deleted after upload
- No data is permanently stored

## 📁 Project Structure

```
Telegram-Unzip-Bot/
├── bot.py                 # Main bot script
├── .env                   # Environment variables (create this)
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── logs/                 # Log files (auto-created)
│   └── bot_YYYY-MM-DD.log
└── downloads/            # Temporary files (auto-created & cleaned)
```

## 📦 Dependencies

```
telethon>=1.34.0          # Telegram client
python-dotenv>=1.0.0      # Environment variables
py7zr>=0.20.0             # 7Z support
rarfile>=4.1              # RAR support
pyzipper>=0.3.6           # Encrypted ZIP support
FastTelethonhelper>=0.1.0 # Fast uploads/downloads
```

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👨‍💻 Developer

Created with ❤️ by [@hellopeter3](https://t.me/hellopeter3)

## 🙏 Acknowledgments

- [Telethon](https://github.com/LonamiWebs/Telethon) - Telegram client library
- [FastTelethon](https://github.com/tulir/mautrix-telegram/tree/master/mautrix_telegram/util/parallel_file_transfer) - Fast file transfers
- [py7zr](https://github.com/miurahr/py7zr) - 7Z archive support
- [rarfile](https://github.com/markokr/rarfile) - RAR archive support

## 📞 Support

- **Telegram**: [@hellopeter3](https://t.me/hellopeter3)
- **Issues**: [GitHub Issues](https://github.com/yourusername/Telegram-Unzip-Bot/issues)

## ⭐ Show Your Support

If this project helped you, please consider giving it a ⭐ on GitHub!

---

**Made with 💙 for the Telegram community**