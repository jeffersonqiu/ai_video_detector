# Setup Guide — Getting Your .env Values

## Step 1 — TELEGRAM_BOT_TOKEN

1. Open Telegram and search for **@BotFather**
2. Start a chat and send `/newbot`
3. When prompted, enter a **name** for your bot (e.g. `AI Video Detector`)
4. When prompted, enter a **username** — must end in `bot` (e.g. `my_ai_detector_bot`)
5. BotFather will reply with a token that looks like:
   ```
   1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
6. Copy that token → paste as `TELEGRAM_BOT_TOKEN`

---

## Step 2 — GEMINI_API_KEY

1. Go to https://aistudio.google.com/app/apikey
2. Sign in with your Google account
3. Click **Create API key**
4. Select **Create API key in new project** (or an existing project)
5. Copy the key → paste as `GEMINI_API_KEY`

> The free tier is sufficient. No billing setup needed for personal use at this volume.

---

## Step 3 — ALLOWED_TELEGRAM_USER_ID

This is your dad's numeric Telegram user ID (not a username — a number like `123456789`).

**Option A — Easiest:**
1. On your dad's phone, open Telegram and search for **@userinfobot**
2. Start a chat and send any message
3. It will reply with his user ID
4. Copy the number → paste as `ALLOWED_TELEGRAM_USER_ID`

**Option B — Via the bot itself:**
1. Complete Steps 1–2 first and run the bot locally (`uv run python run_local.py`)
2. Have your dad send `/start` to the bot
3. Check the terminal — it logs: `INFO: /start from user_id=XXXXXXXXX`
4. Copy that number → paste as `ALLOWED_TELEGRAM_USER_ID`
5. Restart the bot after updating `.env`

---

## Step 4 — WEBHOOK_SECRET

This is a random secret string you generate yourself — it secures the webhook endpoint so only Telegram can call it.

Run this in your terminal:

```bash
cd backend
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output (64 hex characters) → paste as `WEBHOOK_SECRET`

> Keep this private. Anyone with this value can send fake updates to your bot.

---

## Step 5 — RAILWAY_PUBLIC_DOMAIN

**You do not need to set this for local testing.**

Railway sets this automatically when you deploy. It will be the domain Railway assigns to your service, e.g.:

```
your-service-name.up.railway.app
```

To find it after deploying to Railway:
1. Go to your Railway project dashboard
2. Click your service → **Settings** → **Domains**
3. Copy the domain (without `https://`) → paste as `RAILWAY_PUBLIC_DOMAIN` in Railway's environment variable panel

> Do NOT put this in your local `.env`. Only set it in Railway's dashboard.

---

## Step 6 — DAILY_REQUEST_LIMIT (optional)

Default is `50`. This caps how many Gemini API calls the bot makes per day as a cost safety net.

At the expected usage (a few videos per day), 50 is more than enough. You can raise or lower it:

```
DAILY_REQUEST_LIMIT=50
```

Leave it as-is unless you have a reason to change it.

---

## Step 7 — INSTAGRAM_COOKIES_FILE (optional — skip for now)

Skip this initially. Only set it if Instagram downloads start failing with a "login required" error.

If that happens:
1. On your Mac, open **Chrome** and log into Instagram
2. Install the Chrome extension **"Get cookies.txt LOCALLY"**
3. Go to `instagram.com` → click the extension → export as **Netscape format**
4. Save the file as `instagram_cookies.txt`
5. Upload it to Railway as a volume or store it somewhere accessible
6. Set `INSTAGRAM_COOKIES_FILE=/path/to/instagram_cookies.txt`

---

## Local .env file (for testing)

Your `backend/.env` should look like this when ready:

```
TELEGRAM_BOT_TOKEN=1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_TELEGRAM_USER_ID=123456789
WEBHOOK_SECRET=a1b2c3d4e5f6...  (64 hex chars)
DAILY_REQUEST_LIMIT=50
```

Leave `RAILWAY_PUBLIC_DOMAIN` out of the local `.env` — it's not needed for polling mode.

---

## Running locally to test

```bash
cd backend
uv run python run_local.py
```

The bot will start in polling mode. Send a TikTok or Instagram Reel link to it on Telegram to test.

---

## Deploying to Railway

1. Push your code to GitHub
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Select this repo
4. In the service **Variables** tab, add all 5 required env vars:
   - `TELEGRAM_BOT_TOKEN`
   - `GEMINI_API_KEY`
   - `ALLOWED_TELEGRAM_USER_ID`
   - `WEBHOOK_SECRET`
   - `RAILWAY_PUBLIC_DOMAIN` ← copy from Settings → Domains after first deploy
5. Deploy → wait for the health check to pass
6. Check logs for: `Webhook registered: https://your-domain/webhook/...`
7. Send a TikTok link to the bot to verify end-to-end

> **First deploy tip:** Railway may show the domain before the service is healthy. Set `RAILWAY_PUBLIC_DOMAIN` once you see the domain assigned, then redeploy if the webhook wasn't registered on the first attempt.
