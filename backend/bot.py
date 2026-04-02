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

# Matches Instagram Reels and TikTok URLs (including short links and URLs embedded in text)
_URL_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:instagram\.com/reel/\S+|tiktok\.com/\S+|vm\.tiktok\.com/\S+)"
)


def _extract_url(text: str) -> str | None:
    """Extract the first supported URL from a message (handles share text around the link)."""
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,)") if match else None


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


async def run_full_pipeline(url: str) -> tuple[DetectionResult, VideoInfo]:
    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/detector_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    try:
        video_path, video_info = await download_video(url, job_dir)
        frame_paths = await extract_frames_async(video_path, job_dir)
        result = await detect_ai_video(frame_paths)
        return result, video_info
    finally:
        cleanup_job(job_dir)


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
    """Check webhook registration status — useful for diagnosing delivery issues."""
    user = update.effective_user
    if not user or user.id != settings.allowed_telegram_user_id:
        return

    try:
        webhook_info = await context.bot.get_webhook_info()
        url = webhook_info.url or "NOT SET"
        pending = webhook_info.pending_update_count
        last_error = webhook_info.last_error_message or "none"
        await update.message.reply_text(
            f"🔧 *Webhook Status*\n\n"
            f"URL: `{url}`\n"
            f"Pending updates: {pending}\n"
            f"Last error: {last_error}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch webhook info: {e}")


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
        await update.message.reply_text(
            "Please send an Instagram Reel or TikTok link."
        )
        return

    if not check_and_increment_daily_limit():
        await update.message.reply_text(
            "⚠️ Daily analysis limit reached. Try again tomorrow."
        )
        return

    # Acknowledge immediately
    status_msg = await update.message.reply_text("⏳ Got your link! Downloading video...")

    try:
        video_path, video_info = await _download_with_status(url, status_msg)
        result = await _analyse_with_status(video_path, video_info, url, status_msg)
        await _send_result(status_msg, result, video_info)

    except UnsupportedPlatformError:
        await status_msg.edit_text("❌ Only Instagram Reels and TikTok links are supported.")
    except DownloadError as e:
        await status_msg.edit_text(f"❌ Could not download video: {e}")
    except FrameExtractionError:
        await status_msg.edit_text("❌ Could not process video frames. Please try again.")
    except Exception as e:
        logger.exception(f"Pipeline error for URL {url!r}: {e}")
        await status_msg.edit_text("❌ Something went wrong. Please try again.")


async def _download_with_status(url: str, status_msg) -> tuple[str, VideoInfo]:
    """Run the download and return (video_path, video_info), updating the status message."""
    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/detector_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    # Store job_dir in the status_msg object so cleanup can happen later
    status_msg._job_dir = job_dir

    video_path, video_info = await download_video(url, job_dir)

    duration_str = _format_duration(video_info.duration_seconds)
    duration_part = f" · {duration_str}" if duration_str else ""
    desc_part = f"\n_{video_info.description}_" if video_info.description else ""

    await status_msg.edit_text(
        f"🎬 *@{video_info.uploader}*{duration_part}{desc_part}\n\n"
        f"🔍 Analysing frames with AI...",
        parse_mode=ParseMode.MARKDOWN,
    )
    return video_path, video_info


async def _analyse_with_status(
    video_path: str, video_info: VideoInfo, url: str, status_msg
) -> DetectionResult:
    """Run frame extraction + detection, then clean up."""
    job_dir = getattr(status_msg, "_job_dir", None)
    try:
        frame_paths = await extract_frames_async(video_path, job_dir)
        result = await detect_ai_video(frame_paths)
        return result
    finally:
        if job_dir:
            cleanup_job(job_dir)


async def _send_result(status_msg, result: DetectionResult, video_info: VideoInfo) -> None:
    verdict_emoji = {"AI GENERATED": "🤖", "LIKELY REAL": "✅", "UNCERTAIN": "❓"}.get(
        result.verdict, "❓"
    )
    confidence_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(
        result.confidence, "⚪"
    )

    reply = (
        f"{verdict_emoji} *{result.verdict}*\n"
        f"{confidence_emoji} Confidence: *{result.confidence}*\n\n"
        f"📝 {result.reason}\n\n"
        f"— @{video_info.uploader}"
    )
    await status_msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("debug", debug_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return app
