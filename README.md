# AI Video Detector

A private Telegram bot that analyses Instagram Reels and TikTok videos and tells you whether the content is AI-generated — with a confidence rating, one-line reason, and a frame thumbnail so you know exactly which video it's talking about.

Built as a personal project to help non-technical family members navigate the rise of AI-generated content on social media.

---

## Demo

Send a link in Telegram → receive a verdict in ~20 seconds:

```
🤖 AI GENERATED
🟢 Confidence: HIGH

📝 Caption explicitly signals an "AI or Real" challenge and frames show
   background inconsistencies and interpolated motion typical of AI generation.

— @creator_handle
⚡ Flash-Lite · 8,234 tokens · $0.00082
```

The reply includes a frame thumbnail from the video for easy reference.

---

## How It Works

```
Telegram (you or group members)
        │  share Instagram Reel or TikTok link
        ▼
Download video                    yt-dlp
        │
        ▼
Extract audio + 8 frames          ffmpeg
        │
        ▼
Multi-signal AI analysis          Gemini 2.5 Flash-Lite
  ├─ Signal 1: Caption keywords   (detects "AI or Real", tool names, etc.)
  ├─ Signal 2: Visual frames      (artifacts, morphing, uncanny valley)
  └─ Signal 3: Audio              (TTS voice, unnatural pacing)
        │
        │  Low confidence? → escalate to Gemini 2.5 Flash
        │  Gemini blocked? → fall back to Claude Haiku
        ▼
Verdict + thumbnail sent to Telegram
        │
        ▼
/tmp cleanup                      all files deleted immediately
```

---

## Key Design Decisions

**Multi-signal reasoning, not a single pass**
The detector analyses caption, visual frames, and audio as separate signals, then synthesises a weighted verdict. A caption that explicitly says "AI or Real" is treated as near-definitive evidence — it won't be overridden by a realistic-looking visual.

**Three-tier model escalation**
1. Gemini 2.5 Flash-Lite — fast and cheap (~$0.0002/video)
2. Gemini 2.5 Flash — triggered when confidence is LOW or caption signal conflicts with verdict
3. Claude Haiku — fallback when Gemini's model-level safety filter blocks a video

**Frame extraction over video upload**
8 evenly-spaced JPEG frames are sent inline rather than uploading the full video to the Gemini File API. This avoids the upload/poll/delete lifecycle and keeps each call a single round-trip.

**Resilient parsing**
Model responses are parsed with regex that handles markdown formatting and keyword position anywhere in the line. If parsing still fails, a second extraction call asks the model to pull the verdict from its own analysis — inspired by the two-pass retry pattern.

**Group chat support**
The bot can be added to a Telegram group. Any member of a whitelisted group can share links. In groups the bot stays silent when no URL is found, avoiding notification spam.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot | python-telegram-bot 22.7 (webhook mode) |
| Backend | FastAPI + uvicorn |
| Video download | yt-dlp (with Instagram cookie fallback) |
| Frame + audio extraction | ffmpeg |
| Primary AI | Gemini 2.5 Flash-Lite (`google-genai` SDK) |
| Escalation AI | Gemini 2.5 Flash |
| Fallback AI | Claude Haiku (`anthropic` SDK) |
| Package manager | uv |
| Hosting | Railway (Dockerfile deploy) |

---

## Project Structure

```
ai_generated_detector/
├── Dockerfile
├── railway.toml
└── backend/
    ├── main.py               FastAPI app, webhook endpoint, startup
    ├── bot.py                Telegram handlers, pipeline orchestration
    ├── config.py             Pydantic settings from environment variables
    ├── models.py             DetectionResult data model
    ├── rate_limiter.py       Daily API call cap (in-memory)
    ├── run_local.py          Local dev runner (polling mode)
    ├── pyproject.toml
    └── services/
        ├── downloader.py     yt-dlp wrapper — Instagram + TikTok
        ├── frame_extractor.py  ffmpeg wrapper — 8 JPEG frames at 720px
        ├── audio_extractor.py  ffmpeg wrapper — MP3 audio extraction
        ├── detector.py       Multi-signal detection pipeline
        └── cleanup.py        /tmp cleanup after each request
```

---

## Local Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- ffmpeg — `brew install ffmpeg`

### 1. Install dependencies

```bash
cd backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=          # from @BotFather
GEMINI_API_KEY=              # from aistudio.google.com
ANTHROPIC_API_KEY=           # from console.anthropic.com
ALLOWED_TELEGRAM_USER_ID=    # your Telegram numeric ID (message @userinfobot)
WEBHOOK_SECRET=              # any random string
DAILY_REQUEST_LIMIT=50
```

### 3. Run locally

```bash
uv run python run_local.py
```

Bot starts in polling mode. Send any TikTok or Instagram Reel link to test.

---

## Deploying to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Select this repo — Railway detects the Dockerfile automatically
4. Add all required environment variables in the **Variables** tab
5. Copy the assigned domain from **Settings → Domains** → set as `RAILWAY_PUBLIC_DOMAIN`
6. Redeploy → wait for health check to pass
7. Verify in logs: `Webhook registered: https://your-domain/webhook/...`

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `GEMINI_API_KEY` | Yes | From Google AI Studio |
| `ANTHROPIC_API_KEY` | Yes | From Anthropic console — Claude fallback |
| `ALLOWED_TELEGRAM_USER_ID` | Yes | Owner's Telegram user ID (numeric) |
| `WEBHOOK_SECRET` | Yes | Random string securing the webhook endpoint |
| `RAILWAY_PUBLIC_DOMAIN` | Production | Your Railway public domain |
| `DAILY_REQUEST_LIMIT` | No | Max API calls per day (default: 50) |
| `ALLOWED_CHAT_IDS` | No | Comma-separated group chat IDs allowed to use the bot |
| `INSTAGRAM_COOKIES_B64` | No | Base64-encoded cookies.txt for Instagram auth fallback |

---

## Group Chat Setup

To let family or friends use the bot in a shared group:

1. In `@BotFather` → your bot → **Bot Settings → Group Privacy → Turn off**
2. Add the bot to your Telegram group
3. Send `/chatid` in the group (owner only) — the bot replies with the chat ID
4. Add that ID to `ALLOWED_CHAT_IDS` in Railway (e.g. `-1001234567890`)
5. Redeploy — all group members can now share links

---

## Supported Links

| Platform | Example |
|---|---|
| Instagram Reels | `https://www.instagram.com/reel/ABC123/` |
| TikTok | `https://www.tiktok.com/@user/video/123` |
| TikTok short | `https://vm.tiktok.com/ABC123/` |

---

## Cost

| Model | Typical cost per video | When used |
|---|---|---|
| Gemini 2.5 Flash-Lite | ~$0.0002 | Default — all videos |
| Gemini 2.5 Flash | ~$0.0006 | Escalated — low confidence or caption conflict |
| Claude Haiku | ~$0.008 | Fallback — when Gemini is blocked |

At typical personal usage (a few videos per day), the monthly cost is under $0.10.

---

## Limitations

**Instagram reliability** — yt-dlp's Instagram support occasionally breaks when Instagram updates its CDN. Fix: update yt-dlp in the Dockerfile and redeploy. No code changes needed in most cases.

**Detection accuracy** — The models are general-purpose, not specialised deepfake detectors. They reliably catch obvious AI-generated content and videos with explicit AI captions, but may miss subtle face-swaps or high-quality deepfakes with no caption signals.

**In-memory rate limiter** — the daily request counter resets on Railway restart. Acceptable for personal use.
