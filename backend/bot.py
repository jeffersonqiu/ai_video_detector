import logging
import os
import re
import uuid

import discord
from discord.ext import commands

from config import settings
from rate_limiter import check_and_increment_daily_limit
from services.cleanup import cleanup_job
from services.detector import detect_ai_video
from services.downloader import (
    DownloadError,
    UnsupportedPlatformError,
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


def _build_embed(result, video_info) -> discord.Embed:
    verdict_emoji = {"AI GENERATED": "🤖", "LIKELY REAL": "✅", "UNCERTAIN": "❓"}.get(
        result.verdict, "❓"
    )
    confidence_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(
        result.confidence, "⚪"
    )
    if "sonnet" in result.model_used:
        model_label = "🟣 Claude Sonnet (escalated)"
    elif "haiku" in result.model_used:
        model_label = "🟣 Claude Haiku"
    elif "lite" in result.model_used:
        model_label = "⚡ Gemini Flash-Lite"
    else:
        model_label = "🔥 Gemini Flash"

    total_tokens = result.input_tokens + result.output_tokens
    cost_str = f"${result.cost_usd:.5f}" if result.cost_usd > 0 else "—"

    embed_color = {"AI GENERATED": 0xEF4444, "LIKELY REAL": 0x22C55E, "UNCERTAIN": 0xF59E0B}.get(
        result.verdict, 0x94A3B8
    )

    embed = discord.Embed(
        title=f"{verdict_emoji} {result.verdict}",
        color=embed_color,
    )
    embed.add_field(name="Confidence", value=f"{confidence_emoji} {result.confidence}", inline=True)
    embed.add_field(name="Model", value=model_label, inline=True)
    embed.add_field(name="Analysis", value=result.reason, inline=False)
    embed.set_footer(text=f"@{video_info.uploader} · {total_tokens:,} tokens · {cost_str}")
    return embed


def build_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True  # Privileged intent — must be enabled in Discord Dev Portal

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logger.info(
            f"Discord bot ready: {bot.user} | "
            f"message_content intent active: {bot.intents.message_content}"
        )

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        # Only process DMs
        if not isinstance(message.channel, discord.DMChannel):
            return

        if message.author.id not in settings.get_allowed_discord_user_ids():
            logger.warning(f"Rejected message from user_id={message.author.id}")
            await message.channel.send("Sorry, this bot is private.")
            return

        text = (message.content or "").strip()
        url = _extract_url(text)

        if not url:
            await message.channel.send("Please send an Instagram Reel or TikTok link.")
            return

        if not check_and_increment_daily_limit():
            await message.channel.send("⚠️ Daily analysis limit reached. Try again tomorrow.")
            return

        status_msg = await message.channel.send("⏳ Got your link! Downloading video...")

        job_id = str(uuid.uuid4())
        job_dir = f"/tmp/detector_{job_id}"
        os.makedirs(job_dir, exist_ok=True)

        frame_paths: list[str] = []

        try:
            # 1. Download
            video_path, video_info = await download_video(url, job_dir)

            # 2. Show video info while analysing
            duration_str = _format_duration(video_info.duration_seconds)
            duration_part = f" · {duration_str}" if duration_str else ""

            await status_msg.edit(
                content=f"🎬 **@{video_info.uploader}**{duration_part}\n\n🔍 Analysing frames with AI..."
            )

            # 3. Extract frames + detect
            frame_paths = await extract_frames_async(video_path, job_dir)
            result = await detect_ai_video(
                frame_paths,
                caption=video_info.description or None,
                video_path=video_path,
            )

            # 4. Build embed
            embed = _build_embed(result, video_info)

            # 5. Send verdict — attach middle frame as image if available
            middle_frame = frame_paths[len(frame_paths) // 2] if frame_paths else None
            await status_msg.delete()

            if middle_frame and os.path.exists(middle_frame):
                embed.set_image(url="attachment://frame.jpg")
                with open(middle_frame, "rb") as f:
                    file = discord.File(f, filename="frame.jpg")
                    await message.channel.send(file=file, embed=embed)
            else:
                await message.channel.send(embed=embed)

        except UnsupportedPlatformError:
            await status_msg.edit(content="❌ Only Instagram Reels and TikTok links are supported.")
        except DownloadError as e:
            await status_msg.edit(content=f"❌ Could not download video: {e}")
        except FrameExtractionError as e:
            await status_msg.edit(content=f"❌ Could not process video frames: {e}")
        except Exception as e:
            logger.exception(f"Pipeline error for URL {url!r}: {e}")
            await status_msg.edit(content=f"❌ Something went wrong: {type(e).__name__}: {e}")
        finally:
            cleanup_job(job_dir)

    return bot
