import os
import re
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── URL patterns ──────────────────────────────────────────────────────────────
URL_PATTERN = re.compile(
    r"https?://"
    r"(?:www\.)?"
    r"(?:"
    r"youtu(?:be\.com/(?:watch\?v=|shorts/)|\.be/)"
    r"|instagram\.com/(?:p|reel|tv|stories)/"
    r"|tiktok\.com/(?:@[\w.]+/video/|t/)"
    r"|vm\.tiktok\.com/"
    r"|(?:twitter|x)\.com/\w+/status/"
    r")"
    r"[\w\-?=&%./]+"
)

# Max file size Telegram allows for bots (50 MB)
MAX_BYTES = 50 * 1024 * 1024

# YouTube extractor args — bypass bot-detection without cookies
_YT_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["web_creator", "android", "web"],
        "player_skip": ["webpage"],
    }
}


def extract_url(text: str) -> str | None:
    """Return the first supported video URL found in text, or None."""
    m = URL_PATTERN.search(text or "")
    return m.group(0) if m else None


def is_youtube(url: str) -> bool:
    return "youtu" in url


def ydl_opts(out_path: str, url: str = "") -> dict:
    opts = {
        "format": (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]"
            "/best"
        ),
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }

    # YouTube-specific: use multiple player clients to avoid bot-detection
    if is_youtube(url):
        opts["extractor_args"] = _YT_EXTRACTOR_ARGS
        # Use android client as primary — doesn't require PO token
        opts["format"] = (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720][ext=mp4]"
            "/best[height<=720]"
            "/best"
        )

    # Optional cookies file (set COOKIES_FILE env var in Railway)
    cookies_file = os.getenv("COOKIES_FILE")
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = cookies_file

    # Optional cookies from browser (set COOKIES_FROM_BROWSER=chrome/firefox)
    cookies_browser = os.getenv("COOKIES_FROM_BROWSER")
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    return opts


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    url = extract_url(message.text)
    if not url:
        return

    chat_id = message.chat_id
    reply = await message.reply_text("⏳ Завантажую відео…")

    with tempfile.TemporaryDirectory() as tmp:
        out_path = str(Path(tmp) / "video.%(ext)s")

        # For YouTube, try up to 3 different client strategies
        strategies = [ydl_opts(out_path, url)]
        if is_youtube(url):
            # Fallback 1: android_vr client (no PO token needed)
            fb1 = ydl_opts(out_path, url)
            fb1["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
            # Fallback 2: tv_embedded client
            fb2 = ydl_opts(out_path, url)
            fb2["extractor_args"] = {"youtube": {"player_client": ["tv_embedded"]}}
            strategies += [fb1, fb2]

        info = None
        last_error = None

        for i, opts in enumerate(strategies):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                last_error = None
                break  # success
            except yt_dlp.utils.DownloadError as e:
                last_error = e
                logger.warning("Strategy %d failed: %s", i + 1, e)
                # Clean up partial files before retry
                for f in Path(tmp).glob("*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass

        try:
            if last_error:
                raise last_error

            files = list(Path(tmp).glob("*"))
            if not files:
                raise FileNotFoundError("Файл не знайдено після завантаження")
            video_file = max(files, key=lambda f: f.stat().st_size)

            file_size = video_file.stat().st_size
            if file_size > MAX_BYTES:
                await reply.edit_text(
                    "❌ Відео завелике для Telegram (максимум 50 МБ)."
                )
                return

            title = (info or {}).get("title", "")
            caption = f"🎬 {title[:900]}" if title else None

            await reply.edit_text("📤 Надсилаю…")
            with open(video_file, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=caption,
                    reply_to_message_id=message.message_id,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                )
            await reply.delete()

        except yt_dlp.utils.DownloadError as e:
            logger.error("yt-dlp error: %s", e)
            await reply.edit_text(
                "❌ Не вдалося завантажити відео.\n"
                "Можливо посилання приватне або платформа заблокована."
            )
        except Exception as e:
            logger.exception("Unexpected error: %s", e)
            await reply.edit_text("❌ Щось пішло не так. Спробуй ще раз.")


def main():
    token = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
