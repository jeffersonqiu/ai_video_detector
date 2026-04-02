import asyncio
import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from telegram import Update

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger(__name__)

# Built during lifespan startup, used by the webhook handler
_application = None


async def _register_webhook() -> None:
    if not settings.railway_public_domain:
        logger.warning("RAILWAY_PUBLIC_DOMAIN is not set — skipping webhook registration.")
        return
    try:
        webhook_url = (
            f"https://{settings.railway_public_domain}/webhook/{settings.webhook_secret}"
        )
        await _application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook registered: {webhook_url}")
    except Exception:
        logger.exception("Failed to register Telegram webhook.")


def _bootstrap_instagram_cookies() -> None:
    """If INSTAGRAM_COOKIES_B64 is set, decode it and write to /tmp so yt-dlp can use it."""
    b64 = settings.instagram_cookies_b64
    if not b64:
        return
    cookies_path = "/tmp/instagram_cookies.txt"
    try:
        content = base64.b64decode(b64.strip()).decode("utf-8")
        with open(cookies_path, "w") as f:
            f.write(content)
        settings.instagram_cookies_file = cookies_path
        logger.info(f"Instagram cookies written to {cookies_path}")
    except Exception:
        logger.exception("Failed to decode INSTAGRAM_COOKIES_B64 — Instagram may fail.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _application
    _bootstrap_instagram_cookies()
    try:
        from bot import build_application
        _application = build_application()
        await _application.initialize()
        await _application.start()
        logger.info("Telegram bot started.")
        asyncio.create_task(_register_webhook())
    except Exception:
        logger.exception("Bot startup failed — bot will be unavailable, but /health still responds.")

    yield

    if _application:
        try:
            await _application.stop()
            await _application.shutdown()
        except Exception:
            logger.exception("Error during bot shutdown.")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if _application is None:
        raise HTTPException(status_code=503, detail="Bot not initialised")

    data = await request.json()
    update = Update.de_json(data, _application.bot)
    await _application.process_update(update)
    return {"ok": True}
