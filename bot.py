"""Ortflix integrations service for notifications and media automations.

Handles webhooks and callback queries using extensible handler registry.
"""

import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Optional, cast

import uvicorn
from fastapi import FastAPI
from telegram import Bot
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import integrations.media_integrity_integration as media_integrity_integration
import integrations.telegram_callbacks as telegram_callbacks
from app_config import (
    TELEGRAM_PORT,
    TELEGRAM_TOKEN,
    WEBHOOK_HOST,
    WEBHOOK_TOKEN,
    get_telegram_chat_id,
    validate_config,
)
from integrations.manual_actions import build_manual_actions_markup, manual_actions_help_text
from integrations.overseerr_integration import call_overseerr  # noqa: F401
from integrations.overseerr_integration import configure_sender as configure_overseerr_sender
from integrations.overseerr_integration import (
    configure_webhook_token_getter as configure_overseerr_token_getter,
)
from integrations.overseerr_integration import router as overseerr_router
from integrations.radarr_automation import (
    configure_webhook_token_getter as configure_radarr_token_getter,
)
from integrations.radarr_automation import router as radarr_router
from integrations.sonarr_automation import (
    configure_webhook_token_getter as configure_sonarr_token_getter,
)
from integrations.sonarr_automation import router as sonarr_router
from integrations.tautulli_automation import (
    configure_webhook_token_getter as configure_tautulli_token_getter,
)
from integrations.tautulli_automation import router as tautulli_router

# === LOGGING ===
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# === GLOBAL STATE ===
app_telegram: Optional[Application] = None
bot: Optional[Bot] = None
TELEGRAM_CHAT_ID = None


async def manual_actions_command(update, context) -> None:
    """Show manual-actions menu for operator-triggered scripts."""
    if not update.message or not bot:
        return
    chat_id = update.message.chat_id
    if chat_id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized chat for manual actions.")
        return
    await bot.send_message(
        chat_id=chat_id,
        text=manual_actions_help_text(),
        parse_mode="Markdown",
        reply_markup=build_manual_actions_markup(),
    )


# === LIFECYCLE MANAGEMENT ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifecycle."""
    global app_telegram, bot, TELEGRAM_CHAT_ID

    # Validate and initialize
    validate_config()
    TELEGRAM_CHAT_ID = get_telegram_chat_id()

    # Startup
    logger.info("Initializing Telegram bot application...")
    telegram_token = TELEGRAM_TOKEN
    if not telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN is required after configuration validation")
    app_telegram = Application.builder().token(telegram_token).build()
    bot = app_telegram.bot

    # Add handlers
    app_telegram.add_handler(CallbackQueryHandler(telegram_callbacks.callback_query_handler))
    app_telegram.add_handler(CommandHandler("actions", manual_actions_command))

    # Initialize application
    await app_telegram.initialize()
    await app_telegram.start()

    if app_telegram.updater:
        await app_telegram.updater.start_polling(allowed_updates=["callback_query", "message"])
        logger.info("Telegram polling started for callback queries and commands")

    logger.info("Telegram bot initialized successfully")

    yield

    # Shutdown
    logger.info("Shutting down Telegram bot...")
    if app_telegram:
        if app_telegram.updater:
            await app_telegram.updater.stop()
        await app_telegram.stop()
        await app_telegram.shutdown()
    logger.info("Telegram bot shut down successfully")


# === FASTAPI APP ===
try:
    APP_VERSION = version("ortflix-integrations")
except PackageNotFoundError:
    APP_VERSION = "0.0.0+unknown"

app = FastAPI(
    title="Ortflix Integrations API",
    description="REST API for handling Overseerr, Radarr, and media integrity webhooks",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.include_router(overseerr_router)
app.include_router(media_integrity_integration.router)
app.include_router(radarr_router)
app.include_router(sonarr_router)
app.include_router(tautulli_router)


async def send_photo_or_message(image: Optional[str], caption: str, reply_markup=None) -> None:
    """Send a photo (if provided) or a text message to Telegram."""
    if not bot:
        raise RuntimeError("Bot not initialized")
    chat_id = _require_telegram_chat_id()

    send_func = bot.send_photo if image else bot.send_message
    kwargs = {
        "chat_id": chat_id,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup,
    }

    if image:
        kwargs["photo"] = image
        kwargs["caption"] = caption
    else:
        kwargs["text"] = caption
    await send_func(**kwargs)


async def send_text_message(text: str, reply_markup=None) -> None:
    """Send a text-only Telegram message to the configured chat."""
    if not bot:
        raise RuntimeError("Bot not initialized")
    chat_id = _require_telegram_chat_id()
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


def _require_telegram_chat_id() -> int:
    return cast(int, TELEGRAM_CHAT_ID)


configure_overseerr_sender(send_photo_or_message)
media_integrity_integration.configure_sender(send_text_message)
telegram_callbacks.configure_bot_accessor(lambda: bot)
telegram_callbacks.configure_authorized_chat_id_getter(lambda: TELEGRAM_CHAT_ID)
media_integrity_integration.configure_webhook_token_getter(lambda: WEBHOOK_TOKEN)
configure_overseerr_token_getter(lambda: WEBHOOK_TOKEN)
configure_radarr_token_getter(lambda: WEBHOOK_TOKEN)
configure_sonarr_token_getter(lambda: WEBHOOK_TOKEN)
configure_tautulli_token_getter(lambda: WEBHOOK_TOKEN)
telegram_callbacks.register_builtin_handlers()


# === REST API ENDPOINTS ===
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "bot_initialized": bot is not None,
        "telegram_connected": app_telegram is not None,
    }


def main():
    """Main function to start FastAPI server with integrations support."""
    logger.info(f"Starting Ortflix Integrations API on {WEBHOOK_HOST}:{TELEGRAM_PORT}")

    uvicorn.run(
        app,
        host=WEBHOOK_HOST,
        port=TELEGRAM_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
