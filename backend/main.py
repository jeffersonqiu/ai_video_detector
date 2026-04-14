import asyncio
import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger(__name__)


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
    _bootstrap_instagram_cookies()
    try:
        from bot import build_bot
        discord_bot = build_bot()
        task = asyncio.create_task(discord_bot.start(settings.discord_bot_token))
        logger.info("Discord Gateway bot starting...")
    except Exception:
        logger.exception("Discord bot startup failed — bot will be unavailable, but /health still responds.")
        task = None
        discord_bot = None

    yield

    if discord_bot:
        try:
            await discord_bot.close()
        except Exception:
            logger.exception("Error closing Discord bot.")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
