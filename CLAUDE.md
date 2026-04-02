# AI Video Detector — Project Spec for Claude Code

## Project Purpose

A Telegram bot that lets a non-technical user (single user: dad) send an Instagram
Reel or TikTok link and receive a verdict on whether the video is AI-generated or
real. Zero setup required on the user's side beyond installing Telegram and adding
one bot contact.

---

## Architecture Overview

```
Dad's Android (Telegram)
        │  sends IG/TikTok URL as a message
        ▼
Telegram Bot (python-telegram-bot v20+, async)
        │  receives message, validates URL
        ▼
FastAPI Backend (Railway)
        │
        ├─► yt-dlp  ──────────────────── downloads video to /tmp/<job_id>.mp4
        │
        ├─► ffmpeg ───────────────────── extracts 8 evenly-spaced frames → /tmp/<job_id>/frame_N.jpg
        │
        ├─► google-genai SDK ──────────── sends 8 frames as inline base64 images to Gemini 2.5 Flash-Lite
        │                                 receives structured verdict
        ▼
Telegram Bot replies with formatted verdict message
        │
        ▼
/tmp cleanup — deletes video + frames after response
```

---

## Strategic Decisions (rationale preserved for future changes)

### Why Telegram Bot (not PWA, WhatsApp, native app)
- Zero install for user: just add one bot contact
- Android share sheet natively supports Telegram — dad can share directly from
  Instagram/TikTok app without copying URLs
- Telegram Bot API handles media and text messages cleanly
- Backend is fully under our control; no Meta/WhatsApp API complexity

### Why link-based (not file share)
- Better UX: copy link → paste in Telegram, or use Android share sheet
- Avoids 50MB Telegram file size overhead
- yt-dlp handles URL → video download server-side

### Why yt-dlp for both Instagram and TikTok
- Proven in production: Jefferson's `insta_reels_tools_logger` project already uses
  yt-dlp to download full Instagram Reel audio + metadata on Railway
- TikTok: works without auth for public videos
- Instagram: may require cookies depending on Railway IP reputation. The existing
  project does NOT use explicit cookie auth in its env vars, suggesting unauthenticated
  download works at low volume on Railway. The spec includes optional cookie fallback.

### Why frame extraction (not full video upload to Gemini)
- Avoids Gemini File API complexity (upload → poll → use → delete lifecycle)
- Inline base64 images are simpler: single API call, no async file management
- 8 frames from a 15–30s Reel captures enough temporal signal for AI detection
- 20MB inline data limit (verified from Google docs) is not a concern: 8 JPEG frames
  at ~50KB each = ~400KB, well within limit
- Cost: 8 frames × ~258 tokens = ~2,064 input tokens per request

### Why Gemini 2.5 Flash-Lite (not Flash or Pro)
- Confirmed model string: `gemini-2.5-flash-lite` (verified from Google AI docs)
- $0.10/1M input tokens — cheapest multimodal model available as of 2026
- Sufficient reasoning for binary AI detection classification
- Personal-use cost: <$0.01/month at realistic usage
- Gemini 2.0 Flash deprecated March 6 2026 for new projects — do NOT use

### Why google-genai SDK (not google-generativeai)
- `google-generativeai` is deprecated as of 2025
- `google-genai` is the unified current SDK for both AI Studio and Vertex AI
- Correct import: `from google import genai` / `from google.genai import types`
- Client init: `client = genai.Client()` (reads GEMINI_API_KEY from env automatically)

### Why FastAPI + uv (same as insta_reels_tools_logger)
- Consistent with Jefferson's existing Railway deploy patterns
- `uv` for fast, reproducible dependency management
- Async FastAPI works natively with python-telegram-bot v20+ async model

### Why Railway for hosting
- Jefferson already has Railway account and deploy experience from
  insta_reels_tools_logger
- Dockerfile-based deploy, same pattern as existing project
- Supports persistent env vars for secrets

---

## Tech Stack (all versions verified)

| Component | Choice | Version / Notes |
|---|---|---|
| Language | Python | 3.12 |
| Package manager | uv | latest |
| Web framework | FastAPI | latest |
| Telegram library | python-telegram-bot | 20.x (async) |
| Video downloader | yt-dlp | latest (kept updated) |
| Frame extraction | ffmpeg | system package via apt |
| AI detection | google-genai SDK | latest |
| AI model | Gemini 2.5 Flash-Lite | `gemini-2.5-flash-lite` |
| Hosting | Railway | Dockerfile deploy |

---

## Repository Structure

```
ai-video-detector/
├── backend/
│   ├── main.py                     # FastAPI app entry point
│   ├── bot.py                      # Telegram bot setup, handlers, Application builder
│   ├── services/
│   │   ├── __init__.py
│   │   ├── downloader.py           # yt-dlp wrapper for IG + TikTok
│   │   ├── frame_extractor.py      # ffmpeg wrapper, outputs 8 JPEG frames
│   │   ├── detector.py             # Gemini API call, returns DetectionResult
│   │   └── cleanup.py              # /tmp file cleanup utility
│   ├── models.py                   # Pydantic models: DetectionResult, AnalysisRequest
│   ├── config.py                   # Settings from env vars (pydantic-settings)
│   ├── pyproject.toml              # uv dependencies
│   └── .env.example
├── Dockerfile
├── railway.toml
├── .gitignore
├── .dockerignore
└── CLAUDE.md                       # this file
```

---

## Environment Variables

```
# backend/.env (never commit — use Railway env vars in production)

TELEGRAM_BOT_TOKEN=          # from BotFather
GEMINI_API_KEY=               # from Google AI Studio (aistudio.google.com)
ALLOWED_TELEGRAM_USER_ID=    # dad's Telegram user ID (integer) — whitelist for security
WEBHOOK_SECRET=               # random string, used to secure the FastAPI webhook endpoint
RAILWAY_PUBLIC_DOMAIN=        # set automatically by Railway — used to register webhook URL
INSTAGRAM_COOKIES_FILE=       # optional: path to Netscape cookies.txt if unauthenticated IG fails
DAILY_REQUEST_LIMIT=50        # safety cap on Gemini API calls per day (default 50)
```

### How to get ALLOWED_TELEGRAM_USER_ID
Start a chat with the bot and send `/start`. The bot logs the user ID in startup
messages. Alternatively, message `@userinfobot` on Telegram to get your numeric ID.

### How to get GEMINI_API_KEY
Go to https://aistudio.google.com/app/apikey → Create API key → copy value.

---

## Service Specifications

### 1. `backend/services/downloader.py`

**Purpose:** Download a video from an Instagram Reel or TikTok URL using yt-dlp.
Returns the local file path of the downloaded video.

**Function signature:**
```python
async def download_video(url: str, output_dir: str) -> str:
    """
    Downloads video from Instagram or TikTok URL.

    Args:
        url: Instagram Reel URL or TikTok URL
        output_dir: Directory to save the video (e.g. /tmp/<job_id>/)

    Returns:
        str: absolute path to downloaded .mp4 file

    Raises:
        DownloadError: if yt-dlp fails after retries
    """
```

**yt-dlp options to use:**
```python
ydl_opts = {
    'format': 'best[ext=mp4]/best',          # prefer mp4, fallback to best available
    'outtmpl': f'{output_dir}/video.%(ext)s',
    'quiet': True,
    'no_warnings': True,
    'merge_output_format': 'mp4',
    # Optional cookie support for Instagram — only loaded if INSTAGRAM_COOKIES_FILE is set
    # 'cookiefile': settings.instagram_cookies_file,
}
```

**URL detection logic:**
- If `instagram.com/reel/` in URL → Instagram Reel
- If `tiktok.com/` in URL → TikTok
- If `vm.tiktok.com/` in URL → TikTok short URL (yt-dlp handles redirect automatically)
- Anything else → raise `UnsupportedPlatformError`

**Cookie fallback strategy:**
1. Try download without cookies first
2. If yt-dlp raises `DownloadError` containing "login required" or "rate-limit" →
   check if `INSTAGRAM_COOKIES_FILE` env var is set and file exists
3. If cookies file exists, retry with `cookiefile` option
4. If still fails, raise `DownloadError` with user-friendly message

**Instagram cookies.txt setup (for when unauthenticated fails):**
- Export from Chrome using "Get cookies.txt LOCALLY" extension
- Log into Instagram in Chrome first
- Export as Netscape format
- Upload to Railway as a file or store content in env var and write to disk on startup

**IMPORTANT:** yt-dlp must be kept updated. Add to Dockerfile:
```dockerfile
RUN pip install -U yt-dlp
```
Or in pyproject.toml, pin to a recent version and update monthly.

---

### 2. `backend/services/frame_extractor.py`

**Purpose:** Use ffmpeg (subprocess call) to extract 8 evenly-spaced frames from
a video file. Returns list of JPEG file paths.

**Function signature:**
```python
def extract_frames(video_path: str, output_dir: str, num_frames: int = 8) -> list[str]:
    """
    Extracts evenly-spaced frames from a video file using ffmpeg.

    Args:
        video_path: absolute path to .mp4 file
        output_dir: directory to write JPEG frames
        num_frames: number of frames to extract (default 8)

    Returns:
        list[str]: sorted list of absolute paths to JPEG frames

    Raises:
        FrameExtractionError: if ffmpeg fails
    """
```

**ffmpeg command to use:**

First, probe video duration with ffprobe to get total seconds:
```python
import subprocess, json

probe = subprocess.run(
    ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_path],
    capture_output=True, text=True
)
info = json.loads(probe.stdout)
duration = float(info['streams'][0]['duration'])
```

Then extract frames at evenly-spaced timestamps:
```python
# Calculate frame timestamps: e.g. for 30s video and 8 frames:
# timestamps = [1.875, 5.625, 9.375, 13.125, 16.875, 20.625, 24.375, 28.125]
interval = duration / num_frames
timestamps = [interval * (i + 0.5) for i in range(num_frames)]

for i, ts in enumerate(timestamps):
    frame_path = f"{output_dir}/frame_{i:02d}.jpg"
    subprocess.run([
        'ffmpeg', '-ss', str(ts), '-i', video_path,
        '-frames:v', '1', '-q:v', '2',   # q:v 2 = high quality JPEG
        '-vf', 'scale=720:-1',            # resize to 720px width, maintain aspect ratio
        frame_path
    ], check=True, capture_output=True)
```

**Why 720px width:** Sufficient for visual AI artifact detection. Keeps each frame
~40–80KB, well within the 20MB inline data limit for 8 frames combined.

**ffmpeg availability:** ffmpeg is installed via Dockerfile. Also install ffprobe
(comes with ffmpeg package).

---

### 3. `backend/services/detector.py`

**Purpose:** Send 8 frames to Gemini 2.5 Flash-Lite and return a structured
detection verdict.

**Function signature:**
```python
async def detect_ai_video(frame_paths: list[str]) -> DetectionResult:
    """
    Sends extracted frames to Gemini 2.5 Flash-Lite for AI generation detection.

    Args:
        frame_paths: list of absolute paths to JPEG frame files

    Returns:
        DetectionResult: verdict, confidence, reason, token_count

    Raises:
        DetectionError: if Gemini API call fails
    """
```

**Gemini SDK usage (verified against google-genai docs):**
```python
from google import genai
from google.genai import types

client = genai.Client()  # reads GEMINI_API_KEY from env automatically

async def detect_ai_video(frame_paths: list[str]) -> DetectionResult:
    # Build content parts: text prompt + 8 image parts
    parts = []

    # System-level prompt as first text part
    parts.append(types.Part.from_text(DETECTION_PROMPT))

    # Add each frame as inline base64 image
    for frame_path in frame_paths:
        with open(frame_path, 'rb') as f:
            image_bytes = f.read()
        parts.append(types.Part.from_bytes(
            data=image_bytes,
            mime_type='image/jpeg'
        ))

    response = await client.aio.models.generate_content(
        model='gemini-2.5-flash-lite',
        contents=parts,
        config=types.GenerateContentConfig(
            max_output_tokens=300,
            temperature=0.1,   # low temp for consistent classification output
        )
    )

    return parse_verdict(response.text)
```

**Detection prompt (DETECTION_PROMPT constant):**
```
You are an expert at detecting AI-generated video content. Analyse these video frames carefully.

Look for these AI generation indicators:
- Unnatural skin texture, waxy or overly smooth appearance
- Face morphing, blending, or flickering between frames
- Inconsistent lighting direction or shadows that don't match the scene
- Background elements that are blurred, distorted, or geometrically wrong
- Hair, teeth, or fine details that look artificial or inconsistent
- Eye reflections that don't match light sources
- Unnatural motion or movement that looks interpolated
- Text in the video that is garbled, misspelled, or morphing
- Hands with wrong number of fingers or distorted joints
- Any general uncanny valley quality

Respond using EXACTLY this format and nothing else:
VERDICT: [AI GENERATED / LIKELY REAL / UNCERTAIN]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [One sentence explaining the strongest signal you detected]
```

**Parsing the response:**
```python
def parse_verdict(text: str) -> DetectionResult:
    lines = text.strip().splitlines()
    verdict = confidence = reason = None
    for line in lines:
        if line.startswith("VERDICT:"):
            verdict = line.replace("VERDICT:", "").strip()
        elif line.startswith("CONFIDENCE:"):
            confidence = line.replace("CONFIDENCE:", "").strip()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    if not all([verdict, confidence, reason]):
        # Fallback: return uncertain if parsing fails
        return DetectionResult(
            verdict="UNCERTAIN",
            confidence="LOW",
            reason="Could not parse model response clearly.",
            raw_response=text
        )

    return DetectionResult(verdict=verdict, confidence=confidence, reason=reason, raw_response=text)
```

---

### 4. `backend/models.py`

```python
from pydantic import BaseModel
from typing import Optional

class DetectionResult(BaseModel):
    verdict: str        # "AI GENERATED" | "LIKELY REAL" | "UNCERTAIN"
    confidence: str     # "HIGH" | "MEDIUM" | "LOW"
    reason: str         # one-sentence explanation
    raw_response: Optional[str] = None  # full model output for debugging

class AnalysisRequest(BaseModel):
    url: str
    chat_id: int
```

---

### 5. `backend/services/cleanup.py`

```python
import shutil, os

def cleanup_job(job_dir: str) -> None:
    """Remove the entire job directory (video + frames)."""
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
```

Call `cleanup_job` in a `finally` block after every analysis, whether it succeeds
or fails. Job directory pattern: `/tmp/detector_<uuid4>/`

---

### 6. `backend/bot.py`

**Setup pattern (python-telegram-bot v20+ async):**

The Telegram bot runs as a webhook, not polling, because Railway doesn't support
long-running polling processes reliably. FastAPI receives the webhook POST from
Telegram and passes updates to the Application.

```python
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters
from telegram.constants import ParseMode

# Build Application (done once at startup)
application = (
    Application.builder()
    .token(settings.telegram_bot_token)
    .build()
)
```

**Handlers to implement:**

`/start` command:
```python
async def start_handler(update: Update, context):
    await update.message.reply_text(
        "👋 Hi! I'm an AI video detector.\n\n"
        "Send me an Instagram Reel or TikTok link and I'll tell you "
        "if the video was AI-generated.\n\n"
        "Just paste the link as a message."
    )
```

Message handler (URL detection):
```python
async def message_handler(update: Update, context):
    user_id = update.effective_user.id

    # Security: only respond to whitelisted user
    if user_id != settings.allowed_telegram_user_id:
        await update.message.reply_text("Sorry, this bot is private.")
        return

    text = update.message.text or ""
    if not ("instagram.com/reel" in text or "tiktok.com" in text or "vm.tiktok.com" in text):
        await update.message.reply_text(
            "Please send an Instagram Reel or TikTok link."
        )
        return

    # Acknowledge immediately — processing takes 10–30 seconds
    status_msg = await update.message.reply_text("🔍 Analysing video... this takes ~20 seconds.")

    try:
        result = await run_full_pipeline(url=text.strip(), chat_id=update.effective_chat.id)
        verdict_emoji = {
            "AI GENERATED": "🤖",
            "LIKELY REAL": "✅",
            "UNCERTAIN": "❓"
        }.get(result.verdict, "❓")

        confidence_emoji = {
            "HIGH": "🟢",
            "MEDIUM": "🟡",
            "LOW": "🔴"
        }.get(result.confidence, "⚪")

        reply = (
            f"{verdict_emoji} *{result.verdict}*\n"
            f"{confidence_emoji} Confidence: {result.confidence}\n\n"
            f"📝 {result.reason}"
        )

        await status_msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)

    except UnsupportedPlatformError:
        await status_msg.edit_text("❌ Only Instagram Reels and TikTok links are supported.")
    except DownloadError as e:
        await status_msg.edit_text(f"❌ Could not download video: {str(e)}")
    except Exception as e:
        await status_msg.edit_text("❌ Something went wrong. Please try again.")
        # Log the full error server-side
        logger.exception(f"Pipeline error for URL {text}: {e}")
```

**`run_full_pipeline` function:**
```python
import uuid, os
from services.downloader import download_video
from services.frame_extractor import extract_frames
from services.detector import detect_ai_video
from services.cleanup import cleanup_job

async def run_full_pipeline(url: str, chat_id: int) -> DetectionResult:
    job_id = str(uuid.uuid4())
    job_dir = f"/tmp/detector_{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    try:
        # 1. Download video
        video_path = await download_video(url, job_dir)

        # 2. Extract 8 frames
        frame_paths = extract_frames(video_path, job_dir)

        # 3. Detect AI
        result = await detect_ai_video(frame_paths)

        return result
    finally:
        cleanup_job(job_dir)
```

---

### 7. `backend/main.py`

FastAPI app that:
1. Receives Telegram webhook POST at `/webhook/{secret}`
2. Passes the update to the Application
3. Exposes a `/health` endpoint

```python
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: register webhook with Telegram
    webhook_url = f"https://{settings.railway_public_domain}/webhook/{settings.webhook_secret}"
    await application.bot.set_webhook(url=webhook_url)
    await application.initialize()
    await application.start()
    logger.info(f"Webhook registered: {webhook_url}")
    yield
    # Shutdown
    await application.stop()
    await application.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}
```

---

### 8. `backend/config.py`

```python
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    telegram_bot_token: str
    gemini_api_key: str
    allowed_telegram_user_id: int
    webhook_secret: str
    railway_public_domain: str
    instagram_cookies_file: Optional[str] = None
    daily_request_limit: int = 50

    class Config:
        env_file = ".env"

settings = Settings()
```

---

### 9. Daily Request Limiter

Implement a simple in-memory counter to cap Gemini API calls:

```python
# In config.py or a separate rate_limiter.py
from datetime import date
from collections import defaultdict

_daily_counts: dict[str, int] = defaultdict(int)
_count_date: date = date.today()

def check_and_increment_daily_limit() -> bool:
    """Returns True if request is allowed, False if limit exceeded."""
    global _count_date
    today = date.today()
    if today != _count_date:
        _daily_counts.clear()
        _count_date = today
    key = "gemini_calls"
    if _daily_counts[key] >= settings.daily_request_limit:
        return False
    _daily_counts[key] += 1
    return True
```

Call this before `detect_ai_video()`. If it returns False, reply:
"⚠️ Daily analysis limit reached. Try again tomorrow."

Note: This is an in-memory counter and resets on Railway dyno restart. Acceptable
for a personal single-user tool. If Railway restarts mid-day, the count resets —
not a concern at this usage scale.

---

## pyproject.toml Dependencies

```toml
[project]
name = "ai-video-detector"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "python-telegram-bot>=20.0",
    "yt-dlp>=2024.12.0",
    "google-genai>=1.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "httpx>=0.27.0",
]
```

Note: `ffmpeg` and `ffprobe` are system packages installed via Dockerfile, not pip.

---

## Dockerfile

```dockerfile
FROM python:3.12-slim

# Install system dependencies: ffmpeg (includes ffprobe), curl for healthchecks
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app

# Copy dependency files
COPY backend/pyproject.toml .

# Install Python dependencies
RUN uv pip install --system -r pyproject.toml

# Keep yt-dlp updated (important for Instagram compatibility)
RUN pip install -U yt-dlp

# Copy application code
COPY backend/ .

# Railway sets PORT env var
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

---

## railway.toml

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

---

## .gitignore

```
.env
__pycache__/
*.pyc
.venv/
*.egg-info/
/tmp/
cookies*.txt
```

---

## Deployment Checklist

### 1. Create Telegram Bot
- Message `@BotFather` on Telegram
- `/newbot` → follow prompts → copy bot token → set as `TELEGRAM_BOT_TOKEN`

### 2. Get your Telegram User ID (for ALLOWED_TELEGRAM_USER_ID)
- Message `@userinfobot` on Telegram
- Copy the numeric ID it returns

### 3. Get Gemini API Key
- Go to https://aistudio.google.com/app/apikey
- Create API key → copy → set as `GEMINI_API_KEY`

### 4. Deploy to Railway
- Connect GitHub repo to Railway
- Set all env vars in Railway dashboard:
  - `TELEGRAM_BOT_TOKEN`
  - `GEMINI_API_KEY`
  - `ALLOWED_TELEGRAM_USER_ID`
  - `WEBHOOK_SECRET` (generate: `python -c "import secrets; print(secrets.token_hex(32))"`)
  - `RAILWAY_PUBLIC_DOMAIN` (Railway sets this automatically — also available as `RAILWAY_PUBLIC_DOMAIN` in Railway env)
- Deploy → wait for health check to pass

### 5. Verify webhook registration
After deploy, check Railway logs for:
```
Webhook registered: https://<your-railway-domain>/webhook/<secret>
```

### 6. Test end-to-end
Send a TikTok link to the bot first (higher reliability than Instagram).
Then test with an Instagram Reel link.

### 7. If Instagram fails with "login required"
- On your Mac, open Chrome and log into Instagram
- Install "Get cookies.txt LOCALLY" Chrome extension
- Navigate to instagram.com → export cookies as `instagram_cookies.txt`
- Upload to Railway as a volume or paste content into an env var
- Set `INSTAGRAM_COOKIES_FILE=/app/cookies/instagram_cookies.txt`
- Rebuild and redeploy

---

## Error Handling Matrix

| Scenario | User-visible message |
|---|---|
| Unsupported URL (not IG or TikTok) | "Please send an Instagram Reel or TikTok link." |
| yt-dlp download failure (network) | "❌ Could not download video. Please try again." |
| yt-dlp Instagram auth failure | "❌ Instagram blocked this download. Try a TikTok link or contact admin." |
| ffmpeg frame extraction failure | "❌ Could not process video frames. Please try again." |
| Gemini API error (quota/network) | "❌ Analysis service unavailable. Please try again later." |
| Gemini response parse failure | Returns UNCERTAIN/LOW with note that response was unclear |
| Daily limit exceeded | "⚠️ Daily analysis limit reached. Try again tomorrow." |
| Non-whitelisted user | "Sorry, this bot is private." |

---

## Known Limitations & Maintenance Notes

### Instagram download reliability
yt-dlp Instagram support is maintained by the yt-dlp community and is not
officially supported by Meta. Breakage typically occurs when Instagram changes
its CDN or API, and is usually fixed within days by a yt-dlp update.

**Maintenance action when IG breaks:** Update yt-dlp in Dockerfile and redeploy.
```
RUN pip install -U yt-dlp
```
No code changes needed in most cases.

### AI detection accuracy
Gemini 2.5 Flash-Lite is a general-purpose model, not a specialised deepfake
detector. Accuracy is reasonable for obvious AI-generated content but may miss:
- Subtle face-swap on real footage
- AI-enhanced (not fully generated) videos
- High-quality deepfakes trained to avoid common artifacts

This is appropriate for the use case: helping a non-technical user make a quick
judgement call, not forensic verification.

### Single-user whitelist
The `ALLOWED_TELEGRAM_USER_ID` check ensures only dad can use the bot even if
someone finds the bot username. This is intentional — the bot has no auth layer
beyond this.

### /tmp storage on Railway
Railway ephemeral storage is wiped on redeploy. The `/tmp` directory is used only
for per-request transient files which are cleaned up immediately after each
analysis. No persistent storage needed.

---

## Reference: Verified API Patterns

### google-genai async call with multiple images
```python
from google import genai
from google.genai import types

client = genai.Client()  # GEMINI_API_KEY read from env

response = await client.aio.models.generate_content(
    model='gemini-2.5-flash-lite',
    contents=[
        types.Part.from_text("Your prompt here"),
        types.Part.from_bytes(data=image_bytes_1, mime_type='image/jpeg'),
        types.Part.from_bytes(data=image_bytes_2, mime_type='image/jpeg'),
        # ... up to 8 frames
    ],
    config=types.GenerateContentConfig(
        max_output_tokens=300,
        temperature=0.1,
    )
)
print(response.text)
```

### python-telegram-bot v20+ webhook pattern
```python
from telegram import Update
from telegram.ext import Application

application = Application.builder().token(BOT_TOKEN).build()

# Register webhook
await application.bot.set_webhook(url=WEBHOOK_URL)
await application.initialize()
await application.start()

# Process incoming update
update = Update.de_json(json_data, application.bot)
await application.process_update(update)
```

### yt-dlp async wrapper (run in executor to avoid blocking)
```python
import asyncio
from yt_dlp import YoutubeDL

async def download_video(url: str, output_dir: str) -> str:
    loop = asyncio.get_event_loop()

    def _download():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    return await loop.run_in_executor(None, _download)
```

yt-dlp is synchronous — always wrap in `run_in_executor` when called from async
FastAPI/Telegram handler context.

### ffmpeg subprocess (synchronous, run in executor too)
```python
import asyncio, subprocess

async def extract_frames_async(video_path, output_dir, num_frames=8):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        extract_frames,   # the sync function
        video_path, output_dir, num_frames
    )
```

---

## Cost Reference (as of April 2026)

| Model | Input price | Per-analysis cost (8 frames) | 500 analyses/mo |
|---|---|---|---|
| gemini-2.5-flash-lite | $0.10/1M tokens | ~$0.00021 | ~$0.10 |
| gemini-2.5-flash | $0.30/1M tokens | ~$0.00062 | ~$0.31 |

Always use `gemini-2.5-flash-lite`. Do not change this without good reason.

Output tokens capped at 300 via `max_output_tokens=300`.
