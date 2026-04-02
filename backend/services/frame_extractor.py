import asyncio
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


class FrameExtractionError(Exception):
    pass


def _get_duration(video_path: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FrameExtractionError(f"ffprobe failed: {result.stderr}")

    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    for stream in streams:
        duration = stream.get("duration")
        if duration:
            return float(duration)

    raise FrameExtractionError("Could not determine video duration from ffprobe output.")


def extract_frames(video_path: str, output_dir: str, num_frames: int = 8) -> list[str]:
    """
    Extracts num_frames evenly-spaced JPEG frames from a video using ffmpeg.

    Returns a sorted list of absolute paths to the extracted JPEG files.
    Raises FrameExtractionError if ffmpeg fails.
    """
    duration = _get_duration(video_path)

    interval = duration / num_frames
    timestamps = [interval * (i + 0.5) for i in range(num_frames)]

    frame_paths: list[str] = []
    for i, ts in enumerate(timestamps):
        frame_path = f"{output_dir}/frame_{i:02d}.jpg"
        result = subprocess.run(
            [
                "ffmpeg",
                "-ss", str(ts),
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",          # high quality JPEG
                "-vf", "scale=720:-1", # 720px width, maintain aspect ratio
                frame_path,
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise FrameExtractionError(
                f"ffmpeg failed on frame {i} at t={ts:.2f}s: {result.stderr.decode()}"
            )
        frame_paths.append(frame_path)

    logger.info(f"Extracted {len(frame_paths)} frames from {video_path}")
    return sorted(frame_paths)


async def extract_frames_async(
    video_path: str, output_dir: str, num_frames: int = 8
) -> list[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, extract_frames, video_path, output_dir, num_frames)
