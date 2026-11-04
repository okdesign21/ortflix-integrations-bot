"""Media integrity webhook integration."""

import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import MediaIntegrityWebhook
from .webhook_auth import require_valid_webhook_token
from .webhook_logging import webhook_log_context

logger = logging.getLogger(__name__)

router = APIRouter()

_send_text: Optional[Callable[[str, Any], Awaitable[None]]] = None
_corrupted_files_cache: dict[str, dict] = {}


def _default_webhook_token() -> Optional[str]:
    from app_config import WEBHOOK_TOKEN

    return WEBHOOK_TOKEN


_webhook_token_getter: Callable[[], Optional[str]] = _default_webhook_token


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


MAX_CORRUPTION_CACHE_ENTRIES = _int_env("MEDIA_CORRUPTION_CACHE_MAX", 50)

CORRUPTION_CALLBACK_ID_PREFIX = "show_corrupted_files_id_"


def configure_sender(send_text: Callable[[str, Any], Awaitable[None]]) -> None:
    """Set Telegram text sender callback used by media integrity route."""
    global _send_text
    _send_text = send_text


def configure_webhook_token_getter(getter: Callable[[], Optional[str]]) -> None:
    """Set webhook token accessor for runtime and test compatibility."""
    global _webhook_token_getter
    _webhook_token_getter = getter


def _prune_corruption_cache() -> None:
    """Keep cache bounded by dropping oldest entries first."""
    while len(_corrupted_files_cache) > MAX_CORRUPTION_CACHE_ENTRIES:
        oldest_key = next(iter(_corrupted_files_cache))
        _corrupted_files_cache.pop(oldest_key, None)


@router.post("/api/v1/webhooks/media-check")
async def media_integrity_webhook(
    payload: MediaIntegrityWebhook,
    request: Request,
    x_webhook_token: Optional[str] = Header(None),
):
    """Handle media integrity check alerts."""
    require_valid_webhook_token(_webhook_token_getter(), x_webhook_token)

    if _send_text is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sender not set"
        )

    try:
        if payload.notification_type != "CORRUPTION_DETECTED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid notification type",
            )

        if payload.count == 0:
            logger.info(
                "Media integrity webhook contained no corrupted files",
                extra=webhook_log_context(
                    request,
                    "media-integrity",
                    event="webhook.accepted",
                    notification_type=payload.notification_type,
                    file_count=0,
                ),
            )
            return {"status": "ok"}

        cache_key = f"corrupted_{payload.count}_{time.time_ns()}"
        _corrupted_files_cache[cache_key] = {
            "files": [file.model_dump() for file in payload.files],
            "count": payload.count,
        }
        _prune_corruption_cache()

        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📋 View Details",
                        callback_data=f"{CORRUPTION_CALLBACK_ID_PREFIX}{cache_key}",
                    )
                ]
            ]
        )

        await _send_text(payload.summary_message, reply_markup)
        logger.info(
            "Sent media integrity summary",
            extra=webhook_log_context(
                request,
                "media-integrity",
                event="webhook.accepted",
                notification_type=payload.notification_type,
                file_count=payload.count,
            ),
        )
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(
            "Media integrity webhook handling failed",
            extra=webhook_log_context(
                request,
                "media-integrity",
                event="webhook.failed",
                error_type=type(err).__name__,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from err


def get_latest_corruption_data() -> Optional[dict]:
    """Return latest cached corruption payload for callback UI."""
    if not _corrupted_files_cache:
        return None

    latest_key = next(reversed(_corrupted_files_cache))
    return _corrupted_files_cache[latest_key]


def get_corruption_data(cache_key: str) -> Optional[dict]:
    """Return corruption payload for a specific callback cache key."""
    if not cache_key:
        return None
    return _corrupted_files_cache.get(cache_key)
