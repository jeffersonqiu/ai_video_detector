import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from telegram import Update

from bot import application
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger(__name__)


async def _register_webhook() -> None:
    """Register Telegram webhook. Runs in background so /health responds immediately."""
    if not settings.railway_public_domain:
        logger.warning("RAILWAY_PUBLIC_DOMAIN is not set — skipping webhook registration.")
        return
    try:
        webhook_url = (
            f"https://{settings.railway_public_domain}/webhook/{settings.webhook_secret}"
        )
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook registered: {webhook_url}")
    except Exception:
        logger.exception("Failed to register Telegram webhook.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the bot first so /health is reachable immediately
    await application.initialize()
    await application.start()

    # Register webhook in background — does not block healthcheck
    asyncio.create_task(_register_webhook())

    yield

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
