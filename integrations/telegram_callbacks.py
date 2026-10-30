"""Telegram callback query handlers and queue processing."""

import logging
from typing import Any, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app_config import CALLBACK_HANDLERS, register_callback_handler

from .manual_actions import build_manual_actions_markup, manual_actions_help_text, run_manual_action
from .media_integrity_integration import (
    CORRUPTION_CALLBACK_ID_PREFIX,
    get_corruption_data,
    get_latest_corruption_data,
)
from .overseerr_integration import call_overseerr, call_overseerr_api
from .secret_config import redact_diagnostic

logger = logging.getLogger(__name__)

_get_bot: Optional[Callable[[], Any]] = None
_get_authorized_chat_id: Optional[Callable[[], Optional[int]]] = None
request_queue: list[dict] = []
processing = False


def configure_bot_accessor(get_bot: Callable[[], Any]) -> None:
    """Set accessor used by callback handlers to reach the Telegram bot object."""
    global _get_bot
    _get_bot = get_bot


def configure_authorized_chat_id_getter(get_chat_id: Callable[[], Optional[int]]) -> None:
    """Set accessor used to authorize callback queries to one chat id."""
    global _get_authorized_chat_id
    _get_authorized_chat_id = get_chat_id


def _bot_or_none():
    return _get_bot() if _get_bot else None


async def process_queue() -> None:
    """Process queued approval/decline callbacks sequentially."""
    global processing
    bot = _bot_or_none()
    if processing or not request_queue or not bot:
        return

    processing = True
    queue_item = request_queue.pop(0)
    request_id = queue_item["request_id"]
    action = queue_item["action"]
    chat_id = queue_item["chat_id"]
    callback_id = queue_item["callback_id"]

    try:
        await bot.answer_callback_query(callback_id, text=f"Processing request {request_id}...")
        await call_overseerr(request_id, action)
        message = f"✅ Request {request_id} has been {action}d."
        logger.info("Request %s %sd successfully", request_id, action)
    except Exception as err:
        logger.error("Error processing request %s: %s", request_id, err)
        await bot.answer_callback_query(callback_id, text=f"❌ Failed to {action} request")
        message = f"❌ Failed to {action} request {request_id}."

    try:
        await bot.send_message(chat_id, message)
    except Exception as err:
        logger.error("Failed to send queue result for request %s: %s", request_id, err)
    finally:
        processing = False

    if request_queue:
        await process_queue()


async def handle_approve_decline(action: str, parts: list[str], chat_id: int, query) -> None:
    if len(parts) != 2:
        return
    request_id = parts[1]
    request_queue.append(
        {
            "request_id": request_id,
            "action": action,
            "chat_id": chat_id,
            "callback_id": query.id,
        }
    )
    await process_queue()


async def handle_redownload(action: str, parts: list[str], chat_id: int, query) -> None:
    bot = _bot_or_none()
    if len(parts) != 3 or not bot:
        return

    media_type, media_id = parts[1], parts[2]
    try:
        await query.answer(text="🔄 Creating redownload request...")
        result = await call_overseerr_api(
            "/api/v1/request", json_data={"mediaType": media_type, "mediaId": int(media_id)}
        )
        request_id = result.get("id")
        if not request_id:
            raise Exception("No request ID returned")
        await call_overseerr_api(f"/api/v1/request/{request_id}/approve")
        await bot.send_message(
            chat_id, f"✅ Redownload request created and approved!\nRequest ID: {request_id}"
        )
    except Exception as err:
        logger.error("Redownload request failed: %s", redact_diagnostic(str(err)))
        await query.answer(text="❌ Failed to create redownload request", show_alert=True)
        await bot.send_message(chat_id, "❌ Redownload request failed. Check service availability.")


async def handle_show_corrupted_files(action: str, parts: list[str], chat_id: int, query) -> None:
    bot = _bot_or_none()
    if len(parts) < 3 or not bot:
        return

    await query.answer(text="📋 Loading details...")

    cache_key = ""
    raw_data = query.data or ""
    if raw_data.startswith(CORRUPTION_CALLBACK_ID_PREFIX):
        cache_key = raw_data[len(CORRUPTION_CALLBACK_ID_PREFIX) :]

    # Backward compatibility for historical callback payloads.
    cached_data = get_corruption_data(cache_key) if cache_key else get_latest_corruption_data()
    if not cached_data:
        await bot.send_message(chat_id, "❌ Corrupted files data not found")
        return

    files, count = cached_data["files"], cached_data["count"]
    max_display = 20
    details_msg = f"🔴 *Corrupted Files - {count} Issues*\n\n"
    details_msg += "\n".join(
        f"{i}. `{f['path']}`\n   📏 {f['size']} | ❌ {f['error']}"
        for i, f in enumerate(files[:max_display], 1)
    )

    if len(files) > max_display:
        details_msg += f"\n\n... and {len(files) - max_display} more files"

    details_msg += "\n\n*Options:*"
    reply_markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬇️ Redownload All", callback_data="redownload_all_corrupted")],
            [InlineKeyboardButton("⬇️ Redownload Selected", callback_data="redownload_selected")],
            [InlineKeyboardButton("✖️ Close", callback_data="dismiss")],
        ]
    )
    await bot.send_message(
        chat_id, text=details_msg, parse_mode="Markdown", reply_markup=reply_markup
    )


async def handle_dismiss(action: str, parts: list[str], chat_id: int, query) -> None:
    if len(parts) > 2:
        return
    await query.answer(text="✖️ Alert dismissed")
    try:
        await query.message.delete()
    except Exception as err:
        logger.debug("Unable to delete alert message: %s", err)


async def handle_manual(action: str, parts: list[str], chat_id: int, query) -> None:
    """Handle manual action callbacks."""
    bot = _bot_or_none()
    if not bot:
        return
    if len(parts) < 2:
        await query.answer(text="Invalid manual action", show_alert=True)
        return

    mode = parts[1]
    if mode == "menu":
        await query.answer(text="Manual actions")
        await bot.send_message(
            chat_id,
            text=manual_actions_help_text(),
            parse_mode="Markdown",
            reply_markup=build_manual_actions_markup(),
        )
        return

    if mode != "run" or len(parts) < 3:
        await query.answer(text="Unknown manual action", show_alert=True)
        return

    action_id = "_".join(parts[2:])
    await query.answer(text=f"Running {action_id}...")
    ok, message = await run_manual_action(action_id)
    await bot.send_message(chat_id, text=message, parse_mode="Markdown")
    if not ok:
        logger.warning("Manual action failed: %s", action_id)


def register_builtin_handlers() -> None:
    register_callback_handler("approve", handle_approve_decline)
    register_callback_handler("decline", handle_approve_decline)
    register_callback_handler("redownload", handle_redownload)
    register_callback_handler("show", handle_show_corrupted_files)
    register_callback_handler("dismiss", handle_dismiss)
    register_callback_handler("manual", handle_manual)


async def callback_query_handler(update, context) -> None:
    """Dispatch callback queries to registered handlers."""
    query = update.callback_query
    if not query or not query.data:
        return

    logger.info("Callback query received: %s", query.data)
    parts = query.data.split("_")
    if len(parts) < 1:
        return

    action = parts[0]
    if not query.message or not query.message.chat:
        await query.answer(text="Callback context unavailable.", show_alert=True)
        logger.warning("Rejected callback without message/chat context: %s", query.data)
        return

    chat_id = query.message.chat.id
    allowed_chat_id = _get_authorized_chat_id() if _get_authorized_chat_id else None
    if allowed_chat_id is not None and chat_id != allowed_chat_id:
        await query.answer(text="Unauthorized chat.", show_alert=True)
        logger.warning("Rejected callback from unauthorized chat_id=%s", chat_id)
        return

    handler = CALLBACK_HANDLERS.get(action)
    if handler:
        await handler(action, parts, chat_id, query)
        return

    await query.answer(text="Unknown action.", show_alert=True)
    logger.warning("Rejected callback with unknown action=%s data=%s", action, query.data)
