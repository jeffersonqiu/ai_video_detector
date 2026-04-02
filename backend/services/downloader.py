import asyncio
import logging
import os

from yt_dlp import YoutubeDL

from config import settings

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


class UnsupportedPlatformError(Exception):
    pass


def _is_instagram(url: str) -> bool:
    return "instagram.com/reel/" in url


def _is_tiktok(url: str) -> bool:
    return "tiktok.com/" in url or "vm.tiktok.com/" in url


def _find_downloaded_file(output_dir: str, info: dict) -> str:
    """
    Resolve the actual downloaded file path using multiple fallback strategies,
    matching the pattern from the existing insta_reels_tools project.
    """
    # Strategy 1: info["filepath"] set by yt-dlp post-processor
    filepath = info.get("filepath") if isinstance(info, dict) else None
    if filepath and os.path.exists(filepath):
        return filepath

    # Strategy 2: prepare_filename from the outtmpl template
    # This may not reflect the final merged filename, but try it
    # We can't call prepare_filename here without ydl instance, so skip

    # Strategy 3: glob for the largest non-partial file in output_dir
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


def _download_sync(url: str, output_dir: str, cookiefile: str | None = None) -> str:
    ydl_opts = _build_ydl_opts(output_dir, cookiefile)
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _find_downloaded_file(output_dir, info if isinstance(info, dict) else {})


async def download_video(url: str, output_dir: str) -> str:
    """
    Downloads video from an Instagram Reel or TikTok URL.

    Returns the absolute path to the downloaded video file.
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

        # Instagram auth failure — attempt cookie fallback
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
