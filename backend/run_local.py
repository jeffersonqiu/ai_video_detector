"""
Local development runner — uses polling instead of webhook.

Usage:
    uv run python run_local.py

Requires a .env file with all variables except RAILWAY_PUBLIC_DOMAIN.
"""
import logging

from bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    print("Starting bot in polling mode (local dev)...")
    application = build_application()
    application.run_polling(drop_pending_updates=True, close_loop=False)
