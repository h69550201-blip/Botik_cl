import os
import re
import asyncio
import logging
import tempfile
import glob
from pathlib import Path

from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

import yt_dlp
from instagrapi import Client as InstaClient

import subprocess
import sys

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Optional: Instagram credentials for private content
INSTA_USERNAME = os.environ.get("INSTA_USERNAME", "")
INSTA_PASSWORD = os.environ.get("INSTA_PASSWORD", "")

# ─── URL patterns ────────────────────────────────────────────────────────────

PATTERNS = {
    "tiktok":    re.compile(r"https?://(www\.|vm\.|vt\.)?tiktok\.com/\S+"),
    "youtube":   re.compile(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/\S+"),
    "instagram": re.compile(r"https?://(www\.)?instagram\.com/\S+"),
    "twitter":   re.compile(r"https?://(www\.)?(twitter\.com|x\.com)/\S+"),
}

def detect_platform(url: str) -> str | None:
    for platform, pattern in PATTERNS.items():
        if pattern.search(url):
            return platform
    return None

def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text)

# ─── yt-dlp shared config ─────────────────────────────────────────────────────

def base_ydl_opts(outdir: str) -> dict:
    return {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Best video+audio up to 1080p, merged to mp4
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        # Bypass common bot-detection
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        "extractor_args": {
            # TikTok: use the mobile API endpoint
            "tiktok": {"api_hostname": ["api22-normal-c-alisg.tiktokv.com"]},
        },
        "cookiefile": "cookies.txt" if Path("cookies.txt").exists() else None,
    }

def tiktok_opts(outdir: str) -> dict:
    opts = base_ydl_opts(outdir)
    opts.update({
        # Also grab slideshow images (carousel posts)
        "writethumbnail": True,
        "write_all_thumbnails": True,
    })
    return opts

def instagram_opts(outdir: str) -> dict:
    opts = base_ydl_opts(outdir)
    opts.update({
        "writethumbnail": True,
    })
    return opts

def youtube_opts(outdir: str) -> dict:
    opts = base_ydl_opts(outdir)
    opts.update({
        # Shorts work the same way as regular videos in yt-dlp
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best",
    })
    return opts

def twitter_opts(outdir: str) -> dict:
    opts = base_ydl_opts(outdir)
    opts.update({
        "format": "best[ext=mp4]/best",
    })
    return opts

PLATFORM_OPTS = {
    "tiktok":    tiktok_opts,
    "youtube":   youtube_opts,
    "instagram": instagram_opts,
    "twitter":   twitter_opts,
}

# ─── Instagrapi fallback for Instagram carousels ─────────────────────────────

_insta_client: InstaClient | None = None

def get_insta_client() -> InstaClient | None:
    global _insta_client
    if not INSTA_USERNAME or not INSTA_PASSWORD:
        return None
    if _insta_client is None:
        try:
            cl = InstaClient()
            cl.login(INSTA_USERNAME, INSTA_PASSWORD)
            _insta_client = cl
        except Exception as e:
            logger.warning(f"Instagrapi login failed: {e}")
            return None
    return _insta_client

# ─── Download logic ───────────────────────────────────────────────────────────

async def download(url: str, platform: str, outdir: str) -> dict:
    """
    Returns:
      {"type": "video", "path": str}
      {"type": "slideshow", "images": [str, ...], "audio": str|None}
      {"type": "error", "msg": str}
    """
    opts_fn = PLATFORM_OPTS[platform]
    opts = opts_fn(outdir)

    loop = asyncio.get_event_loop()

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info

    try:
        info = await loop.run_in_executor(None, _run)
    except yt_dlp.utils.DownloadError as e:
        return {"type": "error", "msg": str(e)}
    except Exception as e:
        return {"type": "error", "msg": str(e)}

    if info is None:
        return {"type": "error", "msg": "No info returned by yt-dlp"}

    # Check if this is a slideshow/carousel (TikTok image posts, IG carousels)
    entries = info.get("entries")
    if entries:
        # Playlist / carousel
        images = []
        for entry in entries:
            thumb = entry.get("thumbnail") or entry.get("thumbnails", [{}])[-1].get("url")
            # Try to find downloaded image file
            img_files = glob.glob(f"{outdir}/*.jpg") + glob.glob(f"{outdir}/*.png") + glob.glob(f"{outdir}/*.webp")
            images = img_files
        audio_files = glob.glob(f"{outdir}/*.mp3") + glob.glob(f"{outdir}/*.m4a") + glob.glob(f"{outdir}/*.aac")
        audio = audio_files[0] if audio_files else None
        if images:
            return {"type": "slideshow", "images": sorted(images), "audio": audio}

    # Single video
    # Find the mp4 file
    video_files = glob.glob(f"{outdir}/*.mp4") + glob.glob(f"{outdir}/*.mkv") + glob.glob(f"{outdir}/*.webm")
    if video_files:
        return {"type": "video", "path": video_files[0]}

    # Fallback: check requested filename from info
    ext = info.get("ext", "mp4")
    vid_id = info.get("id", "video")
    path = f"{outdir}/{vid_id}.{ext}"
    if Path(path).exists():
        return {"type": "video", "path": path}

    # Last resort: any file in outdir
    all_files = [f for f in glob.glob(f"{outdir}/*") if Path(f).is_file()]
    if all_files:
        return {"type": "video", "path": all_files[0]}

    return {"type": "error", "msg": "Downloaded file not found on disk"}

# ─── Telegram send helpers ────────────────────────────────────────────────────

MAX_VIDEO_SIZE = 50 * 1024 * 1024   # 50 MB — Telegram bot API limit
MAX_PHOTO_SIZE = 10 * 1024 * 1024   # 10 MB

async def send_result(update: Update, result: dict, url: str):
    chat_id = update.effective_chat.id

    if result["type"] == "error":
        await update.message.reply_text(
            f"❌ Failed to download:\n<code>{result['msg'][:300]}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if result["type"] == "video":
        path = result["path"]
        size = Path(path).stat().st_size
        if size > MAX_VIDEO_SIZE:
            await update.message.reply_text(
                "⚠️ Video is too large for Telegram (>50 MB). Try a shorter clip."
            )
            return
        with open(path, "rb") as f:
            await update.effective_chat.send_video(
                video=f,
                supports_streaming=True,
                caption=f"🎬 <a href='{url}'>Source</a>",
                parse_mode=ParseMode.HTML,
            )

    elif result["type"] == "slideshow":
        images = result["images"]
        audio = result["audio"]

        # Send images as media group (up to 10 per group)
        media_group = []
        for img_path in images[:10]:
            if Path(img_path).stat().st_size <= MAX_PHOTO_SIZE:
                media_group.append(InputMediaPhoto(open(img_path, "rb")))

        if media_group:
            await update.effective_chat.send_media_group(media=media_group)

        if audio and Path(audio).exists():
            with open(audio, "rb") as f:
                await update.effective_chat.send_audio(
                    audio=f,
                    caption=f"🎵 <a href='{url}'>Source</a>",
                    parse_mode=ParseMode.HTML,
                )
        elif not media_group:
            await update.message.reply_text("⚠️ Could not extract images from this post.")

# ─── Message handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    urls = extract_urls(text)

    if not urls:
        return

    for url in urls:
        platform = detect_platform(url)
        if not platform:
            continue

        status_msg = await update.message.reply_text(f"⏳ Downloading from {platform}…")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await download(url, platform, tmpdir)
            try:
                await send_result(update, result, url)
            except Exception as e:
                logger.exception(e)
                await update.message.reply_text(f"❌ Send failed: {str(e)[:200]}")

        await status_msg.delete()

# ─── yt-dlp auto-updater ──────────────────────────────────────────────────────

UPDATE_INTERVAL_HOURS = 6   # check for a new yt-dlp every 6 hours

async def auto_update_ytdlp():
    """Periodically upgrade yt-dlp in-process so the bot never goes stale."""
    while True:
        await asyncio.sleep(UPDATE_INTERVAL_HOURS * 3600)
        logger.info("Checking for yt-dlp update…")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                import importlib
                import yt_dlp as _ydlp
                importlib.reload(_ydlp)
                logger.info("yt-dlp updated successfully.")
            else:
                logger.warning(f"yt-dlp update failed: {result.stderr[:200]}")
        except Exception as e:
            logger.warning(f"yt-dlp auto-update error: {e}")



def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start yt-dlp auto-updater as a background task
    async def on_startup(app):
        asyncio.create_task(auto_update_ytdlp())
        logger.info(f"yt-dlp auto-updater scheduled every {UPDATE_INTERVAL_HOURS}h")

    app.post_init = on_startup
    logger.info("Bot is running…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
