import asyncio
import logging
import os
from dataclasses import dataclass

from yt_dlp import YoutubeDL

from config import settings

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


class UnsupportedPlatformError(Exception):
    pass


@dataclass
class VideoInfo:
    uploader: str
    description: str
    duration_seconds: int | None


def _is_instagram(url: str) -> bool:
    return "instagram.com/reel/" in url


def _is_tiktok(url: str) -> bool:
    return "tiktok.com/" in url or "vm.tiktok.com/" in url


def _extract_video_info(info: dict) -> VideoInfo:
    uploader = (
        info.get("uploader")
        or info.get("channel")
        or info.get("creator")
        or "Unknown"
    )
    description = (info.get("description") or info.get("title") or "").strip()
    # Trim long descriptions
    if len(description) > 120:
        description = description[:117] + "..."
    duration = info.get("duration")
    return VideoInfo(
        uploader=uploader,
        description=description,
        duration_seconds=int(duration) if duration else None,
    )


def _find_downloaded_file(output_dir: str, info: dict) -> str:
    filepath = info.get("filepath") if isinstance(info, dict) else None
    if filepath and os.path.exists(filepath):
        return filepath

    candidates: list[str] = []
    for root, _, files in os.walk(output_dir):
        for name in files:
            if name.endswith((".part", ".ytdl", ".json")):
                continue
            full = os.path.join(root, name)
            if os.path.isfile(full):
                candidates.append(full)

    if not candidates:
        raise DownloadError("Video file was not created by yt-dlp.")

    return max(candidates, key=lambda p: os.path.getsize(p))


def _build_ydl_opts(output_dir: str, cookiefile: str | None = None) -> dict:
    opts: dict = {
        "format": "best[ext=mp4]/best",
        "outtmpl": f"{output_dir}/video.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


def _download_sync(url: str, output_dir: str, cookiefile: str | None = None) -> tuple[str, VideoInfo]:
    ydl_opts = _build_ydl_opts(output_dir, cookiefile)
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        info = info if isinstance(info, dict) else {}
        filepath = _find_downloaded_file(output_dir, info)
        video_info = _extract_video_info(info)
        return filepath, video_info


async def download_video(url: str, output_dir: str) -> tuple[str, VideoInfo]:
    """
    Downloads video from an Instagram Reel or TikTok URL.

    Returns (filepath, VideoInfo).
    Raises UnsupportedPlatformError for non-IG/TikTok URLs.
    Raises DownloadError if download fails after cookie fallback.
    """
    if not (_is_instagram(url) or _is_tiktok(url)):
        raise UnsupportedPlatformError(f"Unsupported URL: {url}")

    loop = asyncio.get_event_loop()

    try:
        return await loop.run_in_executor(None, _download_sync, url, output_dir, None)
    except Exception as exc:
        msg = str(exc).lower()

        if _is_instagram(url) and ("login required" in msg or "rate-limit" in msg or "rate limit" in msg):
            cookies_path = settings.instagram_cookies_file
            if cookies_path and os.path.exists(cookies_path):
                logger.warning("Instagram unauthenticated download failed; retrying with cookies.")
                try:
                    return await loop.run_in_executor(
                        None, _download_sync, url, output_dir, cookies_path
                    )
                except Exception as retry_exc:
                    raise DownloadError(
                        "Instagram blocked this download even with cookies. "
                        "Try refreshing your cookies.txt file."
                    ) from retry_exc
            raise DownloadError(
                "Instagram requires login. Set INSTAGRAM_COOKIES_FILE to enable cookie auth."
            ) from exc

        if "private" in msg:
            raise DownloadError("This video is private or inaccessible.") from exc
        if "unsupported url" in msg:
            raise DownloadError("Invalid or unsupported video URL.") from exc

        raise DownloadError(f"Failed to download video: {exc}") from exc
