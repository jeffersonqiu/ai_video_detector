import asyncio
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class AudioExtractionError(Exception):
    pass


def extract_audio(video_path: str, output_dir: str, max_seconds: int = 60) -> str | None:
    """
    Extract audio from a video file as MP3 using ffmpeg.

    Returns the path to the MP3 file, or None if the video has no audio track.
    Raises AudioExtractionError if ffmpeg fails for any other reason.
    """
    audio_path = os.path.join(output_dir, "audio.mp3")

    result = subprocess.run(
        [
            "ffmpeg",
            "-i", video_path,
            "-vn",              # no video stream
            "-acodec", "libmp3lame",
            "-q:a", "7",        # low-ish quality — keeps file small, enough for analysis
            "-t", str(max_seconds),
            "-y",               # overwrite if exists
            audio_path,
        ],
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        # yt-dlp sometimes downloads video-only streams (no audio track)
        if "no audio" in stderr.lower() or "does not contain" in stderr.lower():
            logger.info("Video has no audio track — skipping audio analysis.")
            return None
        raise AudioExtractionError(f"ffmpeg audio extraction failed: {stderr}")

    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        logger.info("ffmpeg produced an empty audio file — video likely has no audio.")
        return None

    size_kb = os.path.getsize(audio_path) / 1024
    logger.info(f"Audio extracted: {audio_path} ({size_kb:.1f} KB)")
    return audio_path


async def extract_audio_async(
    video_path: str, output_dir: str, max_seconds: int = 60
) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, extract_audio, video_path, output_dir, max_seconds)
