# AI Video Detector 🤖

A private Telegram bot that tells you whether an Instagram Reel or TikTok video was AI-generated.

Send a link → get a verdict in ~20 seconds.

---

## How It Works

```
You (Telegram)
    │  send Instagram Reel or TikTok link
    ▼
Bot downloads the video  (yt-dlp)
    │
    ▼
Extracts 8 frames  (ffmpeg)
    │
    ▼
Sends frames to Gemini 2.5 Flash-Lite for analysis
    │
    ▼
You receive a verdict:
    🤖 AI GENERATED  |  ✅ LIKELY REAL  |  ❓ UNCERTAIN
    🟢 HIGH confidence / 🟡 MEDIUM / 🔴 LOW
    📝 One-line reason
```

---

## Example Output

> 🤖 **AI GENERATED**
> 🟢 Confidence: HIGH
>
> 📝 The subject's skin has a waxy, overly smooth texture and the background geometry is inconsistent between frames.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot | python-telegram-bot 22.7 (webhook) |
| Backend | FastAPI + uvicorn |
| Video download | yt-dlp |
| Frame extraction | ffmpeg |
| AI detection | Gemini 2.5 Flash-Lite (`google-genai` SDK) |
| Package manager | uv |
| Hosting | Railway (Dockerfile deploy) |

---

## Project Structure

```
ai_generated_detector/
├── Dockerfile
├── railway.toml
├── GUIDE.md                  ← step-by-step setup guide
└── backend/
    ├── main.py               ← FastAPI app + webhook endpoint
    ├── bot.py                ← Telegram handlers + pipeline
    ├── config.py             ← environment variable settings
    ├── models.py             ← DetectionResult data model
    ├── rate_limiter.py       ← daily Gemini API call cap
    ├── run_local.py          ← local dev runner (polling mode)
    ├── pyproject.toml
    ├── uv.lock
    └── services/
        ├── downloader.py     ← yt-dlp wrapper (IG + TikTok)
        ├── frame_extractor.py← ffmpeg wrapper, 8 JPEG frames
        ├── detector.py       ← Gemini API call + verdict parsing
        └── cleanup.py        ← /tmp cleanup after each request
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

Fill in your `.env` — see **[GUIDE.md](GUIDE.md)** for where to get each value:

```
TELEGRAM_BOT_TOKEN=        # from @BotFather
GEMINI_API_KEY=            # from aistudio.google.com
ALLOWED_TELEGRAM_USER_ID=  # your dad's Telegram numeric ID
WEBHOOK_SECRET=            # generate one (see GUIDE.md)
DAILY_REQUEST_LIMIT=50
```

### 3. Run the bot

```bash
uv run python run_local.py
```

The bot starts in polling mode. Send a TikTok or Instagram Reel link on Telegram to test it.

---

## Deploying to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Select this repo — Railway will detect the Dockerfile automatically
4. In the service **Variables** tab, add all 5 required env vars (see [GUIDE.md](GUIDE.md))
5. Copy the assigned domain from **Settings → Domains** and set it as `RAILWAY_PUBLIC_DOMAIN`
6. Redeploy → wait for the health check to pass
7. Check logs for `Webhook registered: https://your-domain/webhook/...`
8. Send a TikTok link to the bot to verify

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `GEMINI_API_KEY` | Yes | From Google AI Studio |
| `ALLOWED_TELEGRAM_USER_ID` | Yes | Whitelist — only this user can use the bot |
| `WEBHOOK_SECRET` | Yes | Random secret securing the webhook endpoint |
| `RAILWAY_PUBLIC_DOMAIN` | Production only | Set automatically by Railway |
| `DAILY_REQUEST_LIMIT` | No | Max Gemini calls/day (default: 50) |
| `INSTAGRAM_COOKIES_FILE` | No | Path to cookies.txt if Instagram auth fails |

---

## Supported Links

| Platform | Example URL |
|---|---|
| Instagram Reels | `https://www.instagram.com/reel/ABC123/` |
| TikTok | `https://www.tiktok.com/@user/video/123` |
| TikTok (short) | `https://vm.tiktok.com/ABC123/` |

---

## Cost

Running this for personal use costs virtually nothing.

| Model | Cost per video analysis | 100 videos/month |
|---|---|---|
| Gemini 2.5 Flash-Lite | ~$0.0002 | ~$0.02 |

The `DAILY_REQUEST_LIMIT` acts as a safety cap.

---

## Limitations

- **Instagram reliability** — yt-dlp Instagram support occasionally breaks when Instagram updates its CDN. Fix: update yt-dlp in the Dockerfile and redeploy (no code changes needed).
- **Detection accuracy** — Gemini 2.5 Flash-Lite is a general-purpose model, not a specialised deepfake detector. It catches obvious AI-generated content but may miss subtle face-swaps or high-quality deepfakes.
- **Single user** — the bot only responds to the one Telegram user ID in `ALLOWED_TELEGRAM_USER_ID`. This is intentional.
