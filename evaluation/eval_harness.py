"""
eval_harness.py — IMMUTABLE

Downloads test videos, extracts frames/audio/captions, calls detect() from
test_detector.py, scores results against ground truth, and logs to results.jsonl.

DO NOT MODIFY THIS FILE. Only test_detector.py should be changed.

Run: uv run eval_harness.py
"""

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from yt_dlp import YoutubeDL

# ---------------------------------------------------------------------------
# Setup — load env from evaluation/.env then backend/.env as fallback
# ---------------------------------------------------------------------------

_EVAL_DIR = Path(__file__).parent
_BACKEND_DIR = _EVAL_DIR.parent / "backend"

try:
    from dotenv import load_dotenv
    _env_file = _EVAL_DIR / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
    else:
        load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("harness")

# ---------------------------------------------------------------------------
# Test set parsing
# ---------------------------------------------------------------------------

def _load_test_set() -> list[dict]:
    """Parse test_questions.md → list of {url, label} dicts."""
    path = _EVAL_DIR / "test_questions.md"
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("Video"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        url, label = parts[0], parts[1]
        if "instagram.com" in url or "tiktok.com" in url:
            entries.append({"url": url, "label": label.strip()})
    return entries


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(url: str) -> str:
    """Stable cache key derived from the video ID in the URL."""
    m = re.search(r"/reel/([^/?]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _cache_dir(url: str) -> Path:
    d = _EVAL_DIR / "cache" / _cache_key(url)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Instagram cookies
# ---------------------------------------------------------------------------

def _get_cookies_file() -> str | None:
    cookies_file = os.environ.get("INSTAGRAM_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        return cookies_file

    cookies_b64 = os.environ.get("INSTAGRAM_COOKIES_B64")
    if cookies_b64:
        try:
            content = base64.b64decode(cookies_b64.strip()).decode()
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, prefix="ig_cookies_"
            )
            tmp.write(content)
            tmp.close()
            return tmp.name
        except Exception as e:
            logger.warning(f"Failed to decode INSTAGRAM_COOKIES_B64: {e}")

    return None


# ---------------------------------------------------------------------------
# Download (standalone, no dependency on backend config)
# ---------------------------------------------------------------------------

def _download_video(url: str, output_dir: Path) -> tuple[str, dict]:
    """
    Download a video using yt-dlp. Returns (filepath, info_dict).
    Tries without cookies first; falls back to cookies if rate-limited.
    """
    cookies_file = _get_cookies_file()

    def _build_opts(cookiefile: str | None) -> dict:
        opts: dict = {
            "format": "best[ext=mp4]/best",
            "outtmpl": str(output_dir / "video.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }
        if cookiefile:
            opts["cookiefile"] = cookiefile
        return opts

    def _find_video_file(d: Path, info: dict) -> str:
        fp = info.get("filepath") if isinstance(info, dict) else None
        if fp and os.path.exists(fp):
            return fp
        candidates = [
            str(p) for p in d.iterdir()
            if p.is_file() and p.suffix not in (".part", ".ytdl", ".json")
        ]
        if not candidates:
            raise RuntimeError("yt-dlp produced no video file.")
        return max(candidates, key=lambda p: os.path.getsize(p))

    def _extract_info(info: dict) -> dict:
        uploader = (
            info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown"
        )
        description = (info.get("description") or info.get("title") or "").strip()
        if len(description) > 300:
            description = description[:297] + "..."
        return {
            "uploader": uploader,
            "caption": description,
            "duration_seconds": int(info.get("duration") or 0) or None,
        }

    try:
        with YoutubeDL(_build_opts(cookies_file)) as ydl:
            info = ydl.extract_info(url, download=True) or {}
            filepath = _find_video_file(output_dir, info)
            return filepath, _extract_info(info)
    except Exception as exc:
        msg = str(exc).lower()
        if "instagram" in url and cookies_file is None and (
            "login" in msg or "rate" in msg or "429" in msg
        ):
            raise RuntimeError(
                "Instagram blocked download. Set INSTAGRAM_COOKIES_B64 or "
                "INSTAGRAM_COOKIES_FILE env var."
            ) from exc
        raise


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

# Maximum frames extracted per video (at ~1fps). The detect() function receives
# ALL of these and can use any subset — frames[:4], frames[::2], etc.
_MAX_FRAMES = 30


def _get_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise RuntimeError("Could not determine video duration.")


def _extract_frames(video_path: str, output_dir: Path) -> list[str]:
    """
    Extract up to _MAX_FRAMES frames at ~1fps (evenly spaced).
    Short videos (< _MAX_FRAMES seconds) get 1 frame per second.
    Longer videos get _MAX_FRAMES frames spread evenly.
    """
    duration = _get_duration(video_path)
    num_frames = min(_MAX_FRAMES, max(1, int(duration)))
    interval = duration / num_frames
    timestamps = [interval * (i + 0.5) for i in range(num_frames)]
    frame_paths = []
    for i, ts in enumerate(timestamps):
        frame_path = str(output_dir / f"frame_{i:02d}.jpg")
        result = subprocess.run(
            [
                "ffmpeg", "-ss", str(ts), "-i", video_path,
                "-frames:v", "1", "-q:v", "2", "-vf", "scale=720:-1",
                "-y", frame_path,
            ],
            capture_output=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on frame {i}: {result.stderr.decode()}")
        frame_paths.append(frame_path)
    return sorted(frame_paths)


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def _extract_audio(video_path: str, output_dir: Path) -> str | None:
    audio_path = str(output_dir / "audio.mp3")
    result = subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "7",
            "-t", "60", "-y", audio_path,
        ],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        if "no audio" in stderr.lower() or "does not contain" in stderr.lower():
            return None
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr}")
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        return None
    return audio_path


# ---------------------------------------------------------------------------
# Video preparation (download + extract, with caching)
# ---------------------------------------------------------------------------

def _prepare_video(url: str) -> dict:
    """
    Returns dict with:
        video_path: str
        frames: list[str]   (up to _MAX_FRAMES frames at ~1fps, sorted)
        audio_path: str | None
        caption: str | None
        uploader: str
    """
    cdir = _cache_dir(url)
    info_file = cdir / "info.json"
    # frames_info.json records how many frames were extracted at what max.
    # If absent (old 8-frame cache) or stale, frames are re-extracted.
    frames_info_file = cdir / "frames_info.json"

    # Load cached info if available
    cached_info = json.loads(info_file.read_text()) if info_file.exists() else {}

    # Download if not cached
    video_candidates = [p for p in cdir.iterdir() if p.suffix in (".mp4", ".webm", ".mkv")] if cdir.exists() else []
    if not video_candidates:
        print(f"  ↓ Downloading {_cache_key(url)}...")
        video_path, meta = _download_video(url, cdir)
        info_file.write_text(json.dumps({**meta, "video_path": video_path}))
        cached_info = json.loads(info_file.read_text())
    else:
        video_path = cached_info.get("video_path") or str(max(video_candidates, key=lambda p: p.stat().st_size))

    caption = cached_info.get("caption") or None
    uploader = cached_info.get("uploader", "Unknown")

    # Extract frames if not cached or if extracted under a different MAX_FRAMES setting
    frames_info = json.loads(frames_info_file.read_text()) if frames_info_file.exists() else {}
    frames_stale = frames_info.get("max_frames") != _MAX_FRAMES
    if frames_stale:
        print(f"  🎞 Extracting frames for {_cache_key(url)} (max={_MAX_FRAMES})...")
        # Remove old frames before re-extracting
        for old in cdir.glob("frame_*.jpg"):
            old.unlink()
        extracted = _extract_frames(video_path, cdir)
        frames_info_file.write_text(json.dumps({"max_frames": _MAX_FRAMES, "count": len(extracted)}))

    frames = sorted(str(p) for p in cdir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError(f"No frames found in cache for {url}")

    # Extract audio if not cached (and .mp3 marker not written yet)
    audio_marker = cdir / ".no_audio"
    audio_path_cached = cdir / "audio.mp3"

    if not audio_path_cached.exists() and not audio_marker.exists():
        print(f"  🔊 Extracting audio for {_cache_key(url)}...")
        result = _extract_audio(video_path, cdir)
        if result is None:
            audio_marker.touch()
        # audio.mp3 written by _extract_audio if successful

    audio_path = str(audio_path_cached) if audio_path_cached.exists() else None

    return {
        "url": url,
        "video_path": video_path,
        "frames": frames,
        "audio_path": audio_path,
        "caption": caption,
        "uploader": uploader,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(result: dict, ground_truth: str) -> bool:
    """
    Returns True (PASS) if:
    - verdict matches ground truth
    - confidence is HIGH or MEDIUM
    """
    verdict = (result.get("verdict") or "").strip().upper()
    confidence = (result.get("confidence") or "").strip().upper()

    expected_verdict = "AI GENERATED" if ground_truth.upper() == "AI" else "LIKELY REAL"
    verdict_ok = verdict == expected_verdict
    confidence_ok = confidence in ("HIGH", "MEDIUM")
    return verdict_ok and confidence_ok


# ---------------------------------------------------------------------------
# Results logging
# ---------------------------------------------------------------------------

_RESULTS_FILE = _EVAL_DIR / "results.jsonl"


def _load_best_cost() -> float:
    """Read results.jsonl and return the lowest total_cost_usd among valid strategies."""
    if not _RESULTS_FILE.exists():
        return float("inf")
    best = float("inf")
    for line in _RESULTS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("is_valid") and entry.get("total_cost_usd", float("inf")) < best:
                best = entry["total_cost_usd"]
        except json.JSONDecodeError:
            continue
    return best


def _append_result(entry: dict) -> None:
    with open(_RESULTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Dynamic import of test_detector
# ---------------------------------------------------------------------------

def _load_detector():
    """Load test_detector.py fresh (no import cache)."""
    spec = importlib.util.spec_from_file_location(
        "test_detector", _EVAL_DIR / "test_detector.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("Could not load test_detector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

async def _run_evaluation():
    test_set = _load_test_set()
    if not test_set:
        print("ERROR: No entries found in test_questions.md")
        sys.exit(1)

    # Load detector module fresh
    try:
        detector = _load_detector()
    except Exception as e:
        print(f"\nERROR: Could not load test_detector.py: {e}")
        sys.exit(1)

    strategy_name = getattr(detector, "STRATEGY_NAME", "unnamed")
    strategy_desc = getattr(detector, "STRATEGY_DESCRIPTION", "")
    detect_fn = getattr(detector, "detect", None)
    if detect_fn is None:
        print("ERROR: test_detector.py must define an async def detect(...) function.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Strategy: {strategy_name}")
    if strategy_desc:
        print(f"  {strategy_desc}")
    print(f"{'='*60}\n")

    # Prepare all videos (download + extract, cached)
    prepared = []
    print("Preparing videos (cached after first run)...")
    for entry in test_set:
        try:
            video_data = _prepare_video(entry["url"])
            prepared.append({**entry, **video_data})
        except Exception as e:
            print(f"  ERROR preparing {entry['url']}: {e}")
            sys.exit(1)

    print()

    # Run detect() for each video
    per_video_results = []
    total_cost = 0.0
    passes = 0

    for entry in prepared:
        url = entry["url"]
        label = entry["label"]
        reel_id = _cache_key(url)
        caption_preview = (entry["caption"] or "")[:60] or "(no caption)"

        print(f"Video: {reel_id}  [{label}]")
        print(f"  Caption: {caption_preview}")

        try:
            result = await detect_fn(
                frames=entry["frames"],
                audio_path=entry["audio_path"],
                caption=entry["caption"],
                video_path=entry["video_path"],
            )
        except Exception as e:
            print(f"  ERROR in detect(): {e}")
            result = {
                "verdict": "UNCERTAIN",
                "confidence": "LOW",
                "reason": f"detect() raised an exception: {e}",
                "model_used": "error",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            }

        passed = _score(result, label)
        passes += passed
        cost = result.get("cost_usd", 0.0)
        total_cost += cost

        status = "✓" if passed else "✗"
        verdict = result.get("verdict", "?")
        confidence = result.get("confidence", "?")
        model = result.get("model_used", "?")
        reason = (result.get("reason") or "")[:80]

        print(f"  → {verdict} / {confidence}  {status}  ${cost:.5f}  [{model}]")
        if not passed:
            expected = "AI GENERATED" if label.upper() == "AI" else "LIKELY REAL"
            print(f"    FAIL: expected {expected}, confidence must be HIGH or MEDIUM")
        print(f"    Reason: {reason}")
        print()

        per_video_results.append({
            "url": url,
            "ground_truth": label,
            "verdict": result.get("verdict", ""),
            "confidence": result.get("confidence", ""),
            "reason": result.get("reason", ""),
            "passed": passed,
            "cost_usd": cost,
            "model_used": result.get("model_used", ""),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        })

    # Compute overall result
    is_valid = passes == len(test_set)
    best_before = _load_best_cost()

    # Re-load best after this run (in case this was the first run)
    if is_valid:
        is_new_best = total_cost < best_before
    else:
        is_new_best = False

    # Print summary
    print(f"{'─'*60}")
    print(f"Pass rate:   {passes}/{len(test_set)}  {'✓ VALID' if is_valid else '✗ NOT VALID (requires 5/5)'}")
    print(f"Total cost:  ${total_cost:.5f}")
    if is_valid:
        if is_new_best:
            print(f"🏆 NEW BEST! Previous best was ${best_before:.5f}")
        else:
            print(f"Current best: ${best_before:.5f}  (this run: ${total_cost:.5f} — not an improvement)")
    else:
        print(f"Status: FAILED — fix accuracy before optimising cost.")
    print(f"{'─'*60}\n")

    # Append to results.jsonl
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy_name": strategy_name,
        "strategy_description": strategy_desc,
        "pass_rate": f"{passes}/{len(test_set)}",
        "total_cost_usd": round(total_cost, 7),
        "is_valid": is_valid,
        "is_new_best": is_new_best,
        "per_video": per_video_results,
    }
    _append_result(log_entry)
    print(f"Result appended to results.jsonl")


def main():
    asyncio.run(_run_evaluation())


if __name__ == "__main__":
    main()
