import os
import re
import base64
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

# ── Decode cookies from Base64 env var on startup ─────────────────────────────
COOKIES_PATH: Path | None = None


def _init_cookies() -> None:
    global COOKIES_PATH

    file_path = os.getenv("COOKIES_FILE", "").strip()
    if file_path and Path(file_path).exists():
        COOKIES_PATH = Path(file_path)
        logger.info("Cookies loaded from file: %s", COOKIES_PATH)
        return

    b64 = os.getenv("COOKIES_B64", "").strip()
    if not b64:
        logger.info("No cookies configured")
        return
    try:
        data = base64.b64decode(b64)
        p = Path(tempfile.gettempdir()) / "yt_cookies.txt"
        p.write_bytes(data)
        COOKIES_PATH = p
        logger.info("Cookies decoded from COOKIES_B64 → %s (%d bytes)", p, len(data))
    except Exception as e:
        logger.error("Failed to decode COOKIES_B64: %s", e)


_init_cookies()

# ── URL detection ─────────────────────────────────────────────────────────────
URL_PATTERN = re.compile(r"https?://[^\s]+")

SUPPORTED_DOMAINS = (
    "youtu.be",
    "youtube.com",
    "music.youtube.com",
    "instagram.com",
    "tiktok.com",
    "vt.tiktok.com",
    "vm.tiktok.com",
    "twitter.com",
    "x.com",
)

# These platforms work fine without cookies
NO_COOKIES_DOMAINS = (
    "tiktok.com",
    "vt.tiktok.com",
    "vm.tiktok.com",
    "youtu.be",
    "youtube.com",
    "music.youtube.com",
)

MAX_BYTES = 50 * 1024 * 1024

_YT_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["web_creator", "android", "web"],
        "player_skip": ["webpage"],
    }
}


def extract_url(text: str) -> str | None:
    for m in URL_PATTERN.finditer(text or ""):
        url = m.group(0).rstrip(".,;)")
        if any(d in url for d in SUPPORTED_DOMAINS):
            return url
    return None


def needs_cookies(url: str) -> bool:
    return not any(d in url for d in NO_COOKIES_DOMAINS)


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
        "noplaylist": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }

    # Only attach cookies for platforms that need them (Instagram, X)
    if COOKIES_PATH and needs_cookies(url):
        opts["cookiefile"] = str(COOKIES_PATH)
        logger.info("Using cookies for: %s", url)

    if is_youtube(url):
        opts["extractor_args"] = _YT_EXTRACTOR_ARGS
        opts["format"] = (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720][ext=mp4]"
            "/best[height<=720]"
            "/best"
        )

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

        strategies = [ydl_opts(out_path, url)]
        if is_youtube(url):
            fb1 = ydl_opts(out_path, url)
            fb1["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
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
                break
            except yt_dlp.utils.DownloadError as e:
                last_error = e
                logger.warning("Strategy %d failed: %s", i + 1, e)
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
                raise FileNotFoundError("No file after download")
            video_file = max(files, key=lambda f: f.stat().st_size)

            if video_file.stat().st_size > MAX_BYTES:
                await reply.edit_text("❌ Відео завелике для Telegram (максимум 50 МБ).")
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
    logger.info("Bot started — cookies: %s", COOKIES_PATH or "none")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
