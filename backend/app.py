import os
import re
import asyncio
import logging
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import yt_dlp
from shazamio import Shazam

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://\S+")

# ---------- /start ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your personal Save Bot.\n\n"
        "🎬 Send me a video link (YouTube, TikTok, Instagram, Twitter, etc.)\n"
        "🎵 Send me a voice message or audio file → I'll identify the song with Shazam\n\n"
        "Support: @pony_bots"
    )

# ---------- Helpers ----------
def download_media(url: str, audio_only: bool = False) -> dict:
    """Blocking yt-dlp download. Runs in a thread."""
    outtmpl = str(DOWNLOAD_DIR / "%(title).80s.%(ext)s")

    if audio_only:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "noplaylist": True,
        }
    else:
        # Try to keep under 50MB: prefer 480p mp4
        ydl_opts = {
            "format": "best[height<=480][ext=mp4]/best[height<=480]/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "noplaylist": True,
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

    # yt-dlp postprocessor may change extension
    if audio_only and not filepath.endswith(".mp3"):
        filepath = str(Path(filepath).with_suffix(".mp3"))

    return {
        "path": filepath,
        "title": info.get("title", "media"),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url", url),
    }

async def shazam_recognize(file_path: str):
    shazam = Shazam()
    return await shazam.recognize(file_path)

# ---------- URL Handler ----------
async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    url = URL_REGEX.search(msg.text).group(0)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎬 Video (≤480p)", callback_data=f"vid|{url}"),
        InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"aud|{url}"),
    ]])
    await msg.reply_text("What do you want?", reply_markup=keyboard)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, url = query.data.split("|", 1)
    status = await query.edit_message_text("⏳ Downloading... please wait.")

    try:
        audio_only = (mode == "aud")
        info = await asyncio.to_thread(download_media, url, audio_only)
        path = Path(info["path"])
        size_mb = path.stat().st_size / (1024 * 1024)

        caption = f"🎵 {info['title']}" if audio_only else f"🎬 {info['title']}"

        if size_mb > 49:
            await status.edit_text(
                f"⚠️ File is {size_mb:.1f}MB — too big for Telegram (50MB limit).\n\n"
                f"🔗 Direct link: {info['webpage_url']}"
            )
        else:
            await status.edit_text(f"📤 Uploading ({size_mb:.1f}MB)...")
            with open(path, "rb") as f:
                if audio_only:
                    await query.message.reply_audio(f, caption=caption)
                else:
                    await query.message.reply_video(f, caption=caption)
            await status.delete()

        # cleanup
        path.unlink(missing_ok=True)

    except Exception as e:
        log.exception("download failed")
        await status.edit_text(f"❌ Failed: {e}")

# ---------- Voice / Audio Handler (Shazam) ----------
async def handle_audio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.voice or msg.audio or msg.document
    if not media:
        return

    status = await msg.reply_text("🎧 Listening...")

    try:
        tg_file = await media.get_file()
        local_path = DOWNLOAD_DIR / f"shazam_{msg.message_id}.ogg"
        await tg_file.download_to_drive(local_path)

        result = await shazam_recognize(str(local_path))

        if "track" not in result:
            await status.edit_text("😕 Couldn't recognize this track.")
        else:
            t = result["track"]
            title = t.get("title", "Unknown")
            artist = t.get("subtitle", "Unknown")
            cover = t.get("images", {}).get("coverart")

            text = f"🎵 <b>{title}</b>\n👤 {artist}"
            if cover:
                await status.delete()
                await msg.reply_photo(cover, caption=text, parse_mode="HTML")
            else:
                await status.edit_text(text, parse_mode="HTML")

        local_path.unlink(missing_ok=True)

    except Exception as e:
        log.exception("shazam failed")
        await status.edit_text(f"❌ Error: {e}")

# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env var")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(URL_REGEX),
        handle_url
    ))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.Document.AUDIO,
        handle_audio
    ))

    log.info("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
