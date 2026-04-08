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
    r"|instagram\.com/(?:p|reel|tv)/"
    r"|tiktok\.com/@[\w.]+/video/"
    r"|(?:twitter|x)\.com/\w+/status/"
    r")"
    r"[\w\-?=&%./]+"
)

# Max file size Telegram allows for bots (50 MB)
MAX_BYTES = 50 * 1024 * 1024


def extract_url(text: str) -> str | None:
    """Return the first supported video URL found in text, or None."""
    m = URL_PATTERN.search(text or "")
    return m.group(0) if m else None


def ydl_opts(out_path: str) -> dict:
    return {
        "format": (
            "bestvideo[ext=mp4][filesize<45M]+bestaudio[ext=m4a]"
            "/bestvideo[filesize<45M]+bestaudio"
            "/best[filesize<45M]"
            "/best"
        ),
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": os.getenv("COOKIES_FILE"),   # optional – set in Railway
        # Instagram / TikTok sometimes need a browser UA
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
        # Limit download speed so Railway free tier doesn't stall
        "ratelimit": 5_000_000,   # 5 MB/s
    }


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
        opts = ydl_opts(out_path)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # find the actual downloaded file
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
