import html
import logging
import os
import re
import uuid

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import settings
from models import DetectionResult
from rate_limiter import check_and_increment_daily_limit
from services.cleanup import cleanup_job
from services.detector import detect_ai_video
from services.downloader import (
    DownloadError,
    UnsupportedPlatformError,
    VideoInfo,
    download_video,
)
from services.frame_extractor import FrameExtractionError, extract_frames_async

logger = logging.getLogger(__name__)

# Matches Instagram Reels and TikTok URLs (any subdomain: vm, vt, m, www, etc.)
_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:instagram\.com/reel/|tiktok\.com/)\S+"
)


def _extract_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,)") if match else None


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def _h(text: str) -> str:
    """Escape a string for safe use in Telegram HTML mode."""
    return html.escape(str(text))


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(f"/start from user_id={user.id if user else 'unknown'}")
    await update.message.reply_text(
        "👋 Hi! I'm an AI video detector.\n\n"
        "Send me an Instagram Reel or TikTok link and I'll tell you "
        "if the video was AI-generated.\n\n"
        "Just paste the link as a message."
    )


async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id != settings.allowed_telegram_user_id:
        return
    try:
        webhook_info = await context.bot.get_webhook_info()
        url = webhook_info.url or "NOT SET"
        pending = webhook_info.pending_update_count
        last_error = webhook_info.last_error_message or "none"
        await update.message.reply_text(
            f"🔧 <b>Webhook Status</b>\n\n"
            f"URL: <code>{_h(url)}</code>\n"
            f"Pending updates: {pending}\n"
            f"Last error: {_h(last_error)}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch webhook info: {_h(str(e))}")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    if user.id != settings.allowed_telegram_user_id:
        logger.warning(f"Rejected message from non-whitelisted user_id={user.id}")
        await update.message.reply_text("Sorry, this bot is private.")
        return

    text = (update.message.text or "").strip()
    url = _extract_url(text)

    if not url:
        await update.message.reply_text("Please send an Instagram Reel or TikTok link.")
        return

    if not check_and_increment_daily_limit():
        await update.message.reply_text("⚠️ Daily analysis limit reached. Try again tomorrow.")
        return

    status_msg = await update.message.reply_text("⏳ Got your link! Downloading video...")

    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/detector_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    try:
        # 1. Download
        video_path, video_info = await download_video(url, job_dir)

        # 2. Show video info while analysing
        duration_str = _format_duration(video_info.duration_seconds)
        duration_part = f" · {duration_str}" if duration_str else ""
        desc_part = f"\n<i>{_h(video_info.description)}</i>" if video_info.description else ""

        await status_msg.edit_text(
            f"🎬 <b>@{_h(video_info.uploader)}</b>{_h(duration_part)}{desc_part}\n\n"
            f"🔍 Analysing frames with AI...",
            parse_mode=ParseMode.HTML,
        )

        # 3. Extract frames + detect
        frame_paths = await extract_frames_async(video_path, job_dir)
        result = await detect_ai_video(frame_paths)

        # 4. Send verdict
        verdict_emoji = {"AI GENERATED": "🤖", "LIKELY REAL": "✅", "UNCERTAIN": "❓"}.get(
            result.verdict, "❓"
        )
        confidence_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(
            result.confidence, "⚪"
        )

        await status_msg.edit_text(
            f"{verdict_emoji} <b>{_h(result.verdict)}</b>\n"
            f"{confidence_emoji} Confidence: <b>{_h(result.confidence)}</b>\n\n"
            f"📝 {_h(result.reason)}\n\n"
            f"— @{_h(video_info.uploader)}",
            parse_mode=ParseMode.HTML,
        )

    except UnsupportedPlatformError:
        await status_msg.edit_text("❌ Only Instagram Reels and TikTok links are supported.")
    except DownloadError as e:
        await status_msg.edit_text(f"❌ Could not download video: {_h(str(e))}")
    except FrameExtractionError as e:
        await status_msg.edit_text(f"❌ Could not process video frames: {_h(str(e))}")
    except Exception as e:
        logger.exception(f"Pipeline error for URL {url!r}: {e}")
        # Show actual error so you can diagnose — remove the error detail once stable
        await status_msg.edit_text(
            f"❌ Something went wrong:\n<code>{_h(type(e).__name__)}: {_h(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        cleanup_job(job_dir)


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("debug", debug_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return app
