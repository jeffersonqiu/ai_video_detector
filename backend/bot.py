import logging
import os
import uuid

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import settings
from models import DetectionResult
from rate_limiter import check_and_increment_daily_limit
from services.cleanup import cleanup_job
from services.detector import detect_ai_video
from services.downloader import DownloadError, UnsupportedPlatformError, download_video
from services.frame_extractor import FrameExtractionError, extract_frames_async

logger = logging.getLogger(__name__)


def _is_supported_url(text: str) -> bool:
    return (
        "instagram.com/reel" in text
        or "tiktok.com/" in text
        or "vm.tiktok.com/" in text
    )


async def run_full_pipeline(url: str) -> DetectionResult:
    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/detector_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    try:
        video_path = await download_video(url, job_dir)
        frame_paths = await extract_frames_async(video_path, job_dir)
        result = await detect_ai_video(frame_paths)
        return result
    finally:
        cleanup_job(job_dir)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else "unknown"
    logger.info(f"/start from user_id={user_id}")
    await update.message.reply_text(
        "👋 Hi! I'm an AI video detector.\n\n"
        "Send me an Instagram Reel or TikTok link and I'll tell you "
        "if the video was AI-generated.\n\n"
        "Just paste the link as a message."
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    # Security: only respond to the whitelisted user
    if user.id != settings.allowed_telegram_user_id:
        logger.warning(f"Rejected message from non-whitelisted user_id={user.id}")
        await update.message.reply_text("Sorry, this bot is private.")
        return

    text = (update.message.text or "").strip()
    if not _is_supported_url(text):
        await update.message.reply_text(
            "Please send an Instagram Reel or TikTok link."
        )
        return

    if not check_and_increment_daily_limit():
        await update.message.reply_text(
            "⚠️ Daily analysis limit reached. Try again tomorrow."
        )
        return

    status_msg = await update.message.reply_text(
        "🔍 Analysing video... this takes ~20 seconds."
    )

    try:
        result = await run_full_pipeline(url=text)

        verdict_emoji = {
            "AI GENERATED": "🤖",
            "LIKELY REAL": "✅",
            "UNCERTAIN": "❓",
        }.get(result.verdict, "❓")

        confidence_emoji = {
            "HIGH": "🟢",
            "MEDIUM": "🟡",
            "LOW": "🔴",
        }.get(result.confidence, "⚪")

        reply = (
            f"{verdict_emoji} *{result.verdict}*\n"
            f"{confidence_emoji} Confidence: {result.confidence}\n\n"
            f"📝 {result.reason}"
        )
        await status_msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)

    except UnsupportedPlatformError:
        await status_msg.edit_text(
            "❌ Only Instagram Reels and TikTok links are supported."
        )
    except DownloadError as e:
        await status_msg.edit_text(f"❌ Could not download video: {e}")
    except FrameExtractionError:
        await status_msg.edit_text(
            "❌ Could not process video frames. Please try again."
        )
    except Exception as e:
        logger.exception(f"Pipeline error for URL {text!r}: {e}")
        await status_msg.edit_text("❌ Something went wrong. Please try again.")


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return app


application = build_application()
