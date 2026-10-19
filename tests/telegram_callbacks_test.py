"""Regression tests for Telegram callback dispatch behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_config import CALLBACK_HANDLERS
from integrations.telegram_callbacks import (
    callback_query_handler,
    configure_authorized_chat_id_getter,
    configure_bot_accessor,
    handle_manual,
    handle_redownload,
    handle_show_corrupted_files,
    process_queue,
    register_builtin_handlers,
    request_queue,
)


@pytest.fixture(autouse=True)
def _reset_callback_handlers():
    """Keep callback handler registry isolated between tests."""
    snapshot = dict(CALLBACK_HANDLERS)
    CALLBACK_HANDLERS.clear()
    configure_authorized_chat_id_getter(lambda: None)
    configure_bot_accessor(lambda: None)
    request_queue.clear()
    yield
    configure_authorized_chat_id_getter(lambda: None)
    configure_bot_accessor(lambda: None)
    request_queue.clear()
    CALLBACK_HANDLERS.clear()
    CALLBACK_HANDLERS.update(snapshot)


def _build_update(callback_data: str, chat_id: int = 12345):
    update = MagicMock()
    query = MagicMock()
    query.data = callback_data
    query.id = "cb-1"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.delete = AsyncMock()
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    update.callback_query = query
    return update


@pytest.mark.asyncio
async def test_callback_dispatch_passes_standard_handler_signature():
    handler = AsyncMock()
    CALLBACK_HANDLERS["redownload"] = handler

    update = _build_update("redownload_movie_42")
    await callback_query_handler(update, context=None)

    handler.assert_awaited_once()
    action, parts, chat_id, query = handler.await_args.args
    assert action == "redownload"
    assert parts == ["redownload", "movie", "42"]
    assert chat_id == 12345
    assert query is update.callback_query


@pytest.mark.asyncio
async def test_callback_dispatch_allows_single_token_action():
    handler = AsyncMock()
    CALLBACK_HANDLERS["dismiss"] = handler

    update = _build_update("dismiss")
    await callback_query_handler(update, context=None)

    handler.assert_awaited_once()
    action, parts, chat_id, query = handler.await_args.args
    assert action == "dismiss"
    assert parts == ["dismiss"]
    assert chat_id == 12345
    assert query is update.callback_query


@pytest.mark.asyncio
async def test_builtin_dismiss_handler_acks_and_deletes_message():
    register_builtin_handlers()

    update = _build_update("dismiss")
    query = update.callback_query

    await callback_query_handler(update, context=None)

    query.answer.assert_awaited_once_with(text="✖️ Alert dismissed")
    query.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_dispatch_rejects_unauthorized_chat():
    handler = AsyncMock()
    CALLBACK_HANDLERS["dismiss"] = handler
    configure_authorized_chat_id_getter(lambda: 99999)

    update = _build_update("dismiss", chat_id=12345)
    query = update.callback_query

    await callback_query_handler(update, context=None)

    handler.assert_not_called()
    query.answer.assert_awaited_once_with(text="Unauthorized chat.", show_alert=True)


@pytest.mark.asyncio
async def test_callback_dispatch_rejects_unknown_action():
    update = _build_update("not_registered_123")
    query = update.callback_query

    await callback_query_handler(update, context=None)

    query.answer.assert_awaited_once_with(text="Unknown action.", show_alert=True)


@pytest.mark.asyncio
async def test_callback_dispatch_rejects_missing_message_context():
    update = MagicMock()
    query = MagicMock()
    query.data = "dismiss"
    query.answer = AsyncMock()
    query.message = None
    update.callback_query = query

    await callback_query_handler(update, context=None)

    query.answer.assert_awaited_once_with(text="Callback context unavailable.", show_alert=True)


@pytest.mark.asyncio
async def test_manual_callback_reports_failed_action():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    configure_bot_accessor(lambda: bot)
    query = _build_update("manual_run_audiofix_dry").callback_query

    with patch(
        "integrations.telegram_callbacks.run_manual_action",
        new=AsyncMock(return_value=(False, "action failed")),
    ):
        await handle_manual("manual", ["manual", "run", "audiofix", "dry"], 12345, query)

    query.answer.assert_awaited_once_with(text="Running audiofix_dry...")
    bot.send_message.assert_awaited_once_with(12345, text="action failed", parse_mode="Markdown")


@pytest.mark.asyncio
async def test_redownload_callback_reports_api_failure():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    configure_bot_accessor(lambda: bot)
    query = _build_update("redownload_movie_42").callback_query

    with patch(
        "integrations.telegram_callbacks.call_overseerr_api",
        new=AsyncMock(side_effect=RuntimeError("Seerr unavailable")),
    ):
        await handle_redownload("redownload", ["redownload", "movie", "42"], 12345, query)

    query.answer.assert_any_await(text="🔄 Creating redownload request...")
    query.answer.assert_any_await(text="❌ Failed to create redownload request", show_alert=True)
    bot.send_message.assert_awaited_once_with(
        12345, "❌ Redownload request failed. Check service availability."
    )


@pytest.mark.asyncio
async def test_corruption_callback_reports_missing_cache_data():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    configure_bot_accessor(lambda: bot)
    query = _build_update("show_corrupted_files_missing").callback_query

    with (
        patch("integrations.telegram_callbacks.get_corruption_data", return_value=None),
        patch(
            "integrations.telegram_callbacks.get_latest_corruption_data",
            return_value=None,
        ),
    ):
        await handle_show_corrupted_files(
            "show", ["show", "corrupted", "files", "missing"], 12345, query
        )

    query.answer.assert_awaited_once_with(text="📋 Loading details...")
    bot.send_message.assert_awaited_once_with(12345, "❌ Corrupted files data not found")


@pytest.mark.asyncio
async def test_process_queue_reports_approval_failure():
    bot = MagicMock()
    bot.answer_callback_query = AsyncMock()
    bot.send_message = AsyncMock()
    configure_bot_accessor(lambda: bot)
    request_queue.append(
        {
            "request_id": "123",
            "action": "approve",
            "chat_id": 12345,
            "callback_id": "cb-1",
        }
    )

    with patch(
        "integrations.telegram_callbacks.call_overseerr",
        new=AsyncMock(side_effect=RuntimeError("Seerr timeout")),
    ):
        await process_queue()

    bot.answer_callback_query.assert_any_await("cb-1", text="Processing request 123...")
    bot.answer_callback_query.assert_any_await("cb-1", text="❌ Failed to approve request")
    bot.send_message.assert_awaited_once_with(12345, "❌ Failed to approve request 123.")
