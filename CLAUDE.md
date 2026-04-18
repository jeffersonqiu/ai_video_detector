# AI Video Detector — Project Spec for Claude Code

## Quick Index

| Section | What's in it |
|---|---|
| [Project Purpose](#project-purpose) | What the bot does and who it's for |
| [Architecture Overview](#architecture-overview) | End-to-end pipeline diagram |
| [Repository Structure](#repository-structure) | Every file and its role |
| [Tech Stack](#tech-stack) | Libraries, versions, why each was chosen |
| [Environment Variables](#environment-variables) | All env vars with where to get them |
| [Detection Strategy](#detection-strategy) | 3-tier routing logic, models, costs |
| [Discord Bot Setup](#discord-bot-setup) | Developer Portal steps + dad's setup |
| [Deployment Checklist](#deployment-checklist) | Railway deploy steps |
| [Phase 2 — Cost Optimization](#phase-2--cost-optimization-evaluation) | Autoresearch loop in `evaluation/` |
| [Error Handling](#error-handling-matrix) | Every failure mode and user-visible message |
| [Maintenance Notes](#maintenance-notes) | Instagram breakage, model updates |
| [Key API Patterns](#key-api-patterns) | Verified code snippets for Gemini + discord.py |

---

## Project Purpose

A **Discord bot** that lets a non-technical user (dad) paste an Instagram Reel or
TikTok URL into a shared Discord server and receive an AI-detection verdict.

- Dad joins a private Discord server → pastes link in any channel → bot replies
- You can monitor the same channel to see what he's sending and the results
- Zero technical setup for dad beyond installing Discord and joining the server

---

## Architecture Overview

```
Dad's Android (Discord app)
        │  pastes IG/TikTok URL in #general channel
        ▼
Discord Bot (discord.py v2, persistent Gateway WebSocket)
        │  on_message → validates URL → checks ALLOWED_DISCORD_USER_IDS
        ▼
FastAPI Backend (Railway, always-on)
        │
        ├─► yt-dlp ──────────────── downloads video → /tmp/<job_id>/video.mp4
        │                           Instagram: cookie fallback if auth required
        │
        ├─► ffmpeg ──────────────── extracts ~30 frames at 1fps + MP3 audio
        │                           → /tmp/<job_id>/frame_NN.jpg + audio.mp3
        │
        ├─► Caption pre-screening ─ keyword match on video title/description
        │       STRONG AI signal → free verdict, no model call
        │       Timelapse keyword → escalate to Flash tier
        │       Default          → Flash-Lite tier (3× cheaper)
        │
        ├─► Gemini 2.5 Flash-Lite  cheapest tier: audio + 20 frames
        │   OR Gemini 2.5 Flash    timelapse tier: audio + 18 frames
        │   OR Claude Sonnet 4.6   fallback if Gemini blocked/low confidence
        │
        └─► /tmp cleanup (always runs in finally block)
        ▼
Discord Bot replies with coloured embed card + middle frame image
```

**Key architectural note:** discord.py Gateway runs as an asyncio background task
alongside FastAPI. There is no inbound webhook from Discord — the bot opens an
outbound persistent WebSocket to Discord's Gateway. FastAPI only serves `/health`
for Railway's healthcheck.

---

## Repository Structure

```
ai-video-detector/
├── backend/                        ← Production bot (Phase 1)
│   ├── main.py                     # FastAPI lifespan: starts discord bot + /health endpoint
│   ├── bot.py                      # discord.py on_message handler, embed builder, pipeline call
│   ├── config.py                   # pydantic-settings: reads .env / Railway env vars
│   ├── models.py                   # DetectionResult dataclass
│   ├── rate_limiter.py             # In-memory daily request counter
│   ├── research.py                 # Phase 1 research scripts (reference only)
│   ├── pyproject.toml              # uv dependencies
│   ├── uv.lock                     # Locked dependency versions
│   └── services/
│       ├── detector.py             # AI detection: 3-tier routing, Gemini + Claude calls
│       ├── downloader.py           # yt-dlp wrapper: Instagram + TikTok download
│       ├── frame_extractor.py      # ffmpeg: extracts JPEG frames + MP3 audio
│       ├── audio_extractor.py      # ffmpeg: audio extraction helper
│       └── cleanup.py             # shutil.rmtree /tmp/<job_id>/
├── evaluation/                     ← Phase 2: cost optimization autoresearch loop
│   ├── CLAUDE.md                   # Evaluation-specific spec
│   ├── program.md                  # Agent loop instructions (Karpathy-style)
│   ├── eval_harness.py             # IMMUTABLE: downloads, scores, logs results
│   ├── test_detector.py            # MUTABLE: the strategy the agent optimises
│   ├── test_questions.md           # 5 labeled test videos + ground truth
│   ├── results.jsonl               # Append-only experiment log (14 trials)
│   ├── plot_results.py             # Generates cost_vs_trial.png chart
│   ├── cost_vs_trial.png           # Chart of all 14 trials
│   └── pyproject.toml              # Separate uv project for eval dependencies
├── Dockerfile                      # python:3.12-slim + ffmpeg + uv
├── railway.toml                    # healthcheckPath=/health, ON_FAILURE restart
├── .gitignore
├── .dockerignore
└── CLAUDE.md                       ← THIS FILE
```

---

## Tech Stack

| Component | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Package manager | uv | Fast, reproducible lockfile |
| Web framework | FastAPI | Async, healthcheck endpoint only |
| Bot interface | discord.py v2.7 | Gateway WebSocket, `on_message` handler |
| Video downloader | yt-dlp | Instagram + TikTok; keep updated monthly |
| Frame/audio extraction | ffmpeg + ffprobe | System package in Dockerfile |
| Primary AI | google-genai SDK (Gemini 2.5 Flash / Flash-Lite) | `from google import genai` |
| Fallback AI | anthropic SDK (Claude Sonnet 4.6 / Haiku) | Only if Gemini blocked or low confidence |
| Hosting | Railway | Dockerfile deploy, persistent service (no sleep) |

**Why discord.py Gateway (not Telegram webhook):**
- Plain-text DMs require Gateway — Discord's interactions webhook only supports slash commands
- Railway runs persistent services (no sleep), so a long-lived WebSocket is fine
- Simpler than Telegram: no webhook registration, no `WEBHOOK_SECRET`, no `RAILWAY_PUBLIC_DOMAIN`

**Why google-genai (not google-generativeai):**
- `google-generativeai` is deprecated as of 2025
- `google-genai` is the current unified SDK
- `genai.Client(api_key=settings.gemini_api_key)` — must pass key explicitly; pydantic-settings does NOT export to `os.environ`

---

## Environment Variables

```bash
# backend/.env  (never commit — set in Railway dashboard for production)

DISCORD_BOT_TOKEN=          # from Discord Dev Portal → Bot tab → Reset Token
ALLOWED_DISCORD_USER_IDS=   # comma-separated Discord user IDs (yours + dad's)
GEMINI_API_KEY=              # from https://aistudio.google.com/app/apikey
ANTHROPIC_API_KEY=           # from https://console.anthropic.com/
INSTAGRAM_COOKIES_B64=       # base64-encoded Netscape cookies.txt (optional, for IG auth)
DAILY_REQUEST_LIMIT=50       # safety cap on AI calls per day (resets on restart)
```

**Getting Discord user IDs:**
Discord app → Settings → Advanced → **Developer Mode ON** → right-click any username → Copy User ID (64-bit integer).

**Getting INSTAGRAM_COOKIES_B64:**
```bash
base64 -i instagram_cookies.txt | tr -d '\n'   # paste output as env var value
```

---

## Detection Strategy

Current strategy (best found after 14 autoresearch trials): **3-tier caption routing**

```
Caption signal check (free, no model call)
    │
    ├── STRONG AI keywords (e.g. "AI generated", "Sora", "Kling AI")
    │       → return AI GENERATED / HIGH immediately. Cost: $0.00
    │
    ├── Timelapse keywords (e.g. "timelapse", "transformation", "#transform")
    │       → Gemini 2.5 Flash + audio + 18 frames
    │          Cost: ~$0.0016/video
    │
    └── Default (neutral caption or no caption)
            → Gemini 2.5 Flash-Lite + audio + 15 frames
               Cost: ~$0.0003/video
```

**Total cost across 5-video test set: $0.00654** (51% reduction from baseline $0.01341)

Model cost reference:

| Model | Input $/1M | Output $/1M |
|---|---|---|
| `gemini-2.5-flash-lite` | $0.10 | $0.40 |
| `gemini-2.5-flash` | $0.30 | $1.00 |
| `claude-haiku-4-5-20251001` | $0.80 | $4.00 |
| `claude-sonnet-4-6` | $3.00 | $15.00 |

**Key findings from Phase 2 research:**
- DWmajXxjF7S (flowerbed timelapse) is the hardest test case — audio is decisive, needs 18+ frames
- Flash-Lite cannot reliably detect subtle AI timelapses — must route to Flash
- Full 600-token multi-signal prompt required for Flash accuracy; 80-token short prompt breaks results
- `genai.Client()` must receive `api_key=` explicitly — it does NOT read from `os.environ` when using pydantic-settings

---

## Discord Bot Setup

### Developer Portal (one-time)

1. discord.com/developers/applications → **New Application**
2. **Bot** tab → Reset Token → copy as `DISCORD_BOT_TOKEN`
3. **Bot** tab → Privileged Gateway Intents → enable **MESSAGE CONTENT INTENT** ← critical
4. **Installation** tab → Default Install Settings → **Guild Install**
5. **OAuth2 → URL Generator** → Scopes: `bot` → Permissions: `Send Messages`, `Embed Links`, `Attach Files`
6. Open generated URL in browser → select your private server → Authorise

### Dad's Setup (one-time)

1. Install Discord (Google Play / App Store)
2. Create Discord account
3. Receive server invite link from you (right-click server → Invite People → copy `discord.gg/xxx` link)
4. Join the server → post any Instagram Reel or TikTok URL in `#general`
5. Add dad's Discord user ID to `ALLOWED_DISCORD_USER_IDS` in Railway

---

## Deployment Checklist

1. **Discord Dev Portal** — complete setup above, copy bot token
2. **Railway env vars** — set all variables from the Environment Variables section
3. **Push to GitHub** — Railway auto-deploys on push to `main`
4. **Check Railway logs** for:
   ```
   Discord bot ready: AI Video Detector#XXXX | message_content intent active: True
   ```
   If `message_content intent active: False` → enable MESSAGE CONTENT INTENT in Dev Portal
5. **Test** — post a TikTok URL in the server channel, expect embed reply within ~20 seconds

**If Instagram download fails:**
- Export cookies from Chrome (logged into Instagram) using "Get cookies.txt LOCALLY" extension
- `base64 -i instagram_cookies.txt | tr -d '\n'` → paste as `INSTAGRAM_COOKIES_B64` in Railway

---

## Phase 2 — Cost Optimization (evaluation/)

A Karpathy-style autoresearch loop that autonomously improves the detection strategy.

**Pattern:** one mutable file (`test_detector.py`) + immutable harness (`eval_harness.py`) + ratchet (only keep cheaper valid strategies) + agent explores freely.

**Constraints:** 100% accuracy on 5 labeled videos, MEDIUM+ confidence, UNCERTAIN always fails.

**To run:** `cd evaluation && uv run eval_harness.py`

**To launch agent:** "Read `evaluation/program.md` and optimise `test_detector.py`."

Results logged to `evaluation/results.jsonl` (append-only, 14 trials completed).

---

## Error Handling Matrix

| Scenario | User-visible message |
|---|---|
| Non-whitelisted Discord user | "Sorry, this bot is private." |
| No URL in message | "Please send an Instagram Reel or TikTok link." |
| Daily limit exceeded | "⚠️ Daily analysis limit reached. Try again tomorrow." |
| yt-dlp download failure | "❌ Could not download video: {reason}" |
| Unsupported platform | "❌ Only Instagram Reels and TikTok links are supported." |
| ffmpeg frame extraction failure | "❌ Could not process video frames: {reason}" |
| Any unexpected exception | "❌ Something went wrong: {ExceptionType}: {message}" |
| Gemini blocked by safety filter | Returns UNCERTAIN / LOW with "Gemini blocked this content" |
| Response parse failure | Two-pass retry → if still fails, UNCERTAIN / LOW |

---

## Maintenance Notes

**yt-dlp updates (monthly):** Instagram support breaks when Meta changes CDN. Fix: bump `yt-dlp` version in `backend/pyproject.toml`, run `uv lock`, push → Railway redeploys.

**Discord MESSAGE CONTENT INTENT:** If the bot stops responding to messages silently, check Railway logs for `message_content intent active: False`. Re-enable in Discord Dev Portal → Bot → Privileged Gateway Intents.

**Gemini model deprecation:** `gemini-2.0-flash` was deprecated March 2026. Always use `gemini-2.5-flash` or `gemini-2.5-flash-lite`. Check Google AI docs before changing model strings.

**AI detection accuracy limits:** The bot catches obvious AI-generated content well. It may miss subtle face-swaps, AI-enhanced (not fully generated) videos, or high-quality deepfakes. This is appropriate for a quick personal judgement tool.

---

## Key API Patterns

### Gemini (google-genai) — MUST pass api_key explicitly

```python
from google import genai
from google.genai import types
from config import settings

client = genai.Client(api_key=settings.gemini_api_key)
# DO NOT use genai.Client() with no args — pydantic-settings does not set os.environ

response = await client.aio.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=[
        types.Part.from_text(text="your prompt"),           # keyword arg required
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
        types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg"),
    ],
    config=types.GenerateContentConfig(
        max_output_tokens=512,
        temperature=0.1,
        thinking_config=types.ThinkingConfig(thinking_budget=0),  # disable thinking
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        ],
    ),
)
text = response.text  # None if blocked by safety filter
input_tokens = response.usage_metadata.prompt_token_count
output_tokens = response.usage_metadata.candidates_token_count
```

### discord.py v2 — Gateway bot with FastAPI

```python
import discord
from discord.ext import commands

def build_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True  # requires Privileged Intent in Dev Portal

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logger.info(f"Discord bot ready: {bot.user} | message_content: {bot.intents.message_content}")

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        # ... handler logic
    
    return bot

# In FastAPI lifespan:
discord_bot = build_bot()
task = asyncio.create_task(discord_bot.start(settings.discord_bot_token))
```

### discord.py — Sending embed with image attachment

```python
embed = discord.Embed(title="🤖 AI GENERATED", color=0xEF4444)
embed.add_field(name="Confidence", value="🟢 HIGH", inline=True)
embed.add_field(name="Analysis", value="reason text", inline=False)
embed.set_image(url="attachment://frame.jpg")

with open(frame_path, "rb") as f:
    file = discord.File(f, filename="frame.jpg")
await message.channel.send(file=file, embed=embed)
```

### yt-dlp async wrapper

```python
import asyncio
from yt_dlp import YoutubeDL

async def download_video(url: str, output_dir: str):
    loop = asyncio.get_event_loop()
    def _download():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    return await loop.run_in_executor(None, _download)
# yt-dlp is synchronous — always wrap in run_in_executor
```
