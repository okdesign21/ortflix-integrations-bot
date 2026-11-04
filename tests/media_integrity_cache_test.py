"""Tests for media integrity cache bounds and latest-item retrieval."""

import pytest

from integrations.media_integrity_integration import (
    CORRUPTION_CALLBACK_ID_PREFIX,
    _corrupted_files_cache,
    configure_sender,
    get_corruption_data,
    get_latest_corruption_data,
    media_integrity_webhook,
)
from integrations.models import CorruptedFileInfo, MediaIntegrityWebhook


@pytest.mark.asyncio
async def test_media_corruption_cache_is_bounded(monkeypatch):
    import integrations.media_integrity_integration as module

    sent_messages = []

    async def _sender(text, reply_markup):
        sent_messages.append((text, reply_markup))

    configure_sender(_sender)
    _corrupted_files_cache.clear()
    monkeypatch.setattr(module, "MAX_CORRUPTION_CACHE_ENTRIES", 2)

    for idx in range(3):
        payload = MediaIntegrityWebhook(
            notification_type="CORRUPTION_DETECTED",
            summary_message=f"summary-{idx}",
            count=idx + 1,
            files=[CorruptedFileInfo(path=f"/media/file{idx}.mkv", size="1GB", error="err")],
        )
        result = await media_integrity_webhook(payload, request=None)
        assert result["status"] == "ok"

    assert len(_corrupted_files_cache) == 2
    assert len(sent_messages) == 3


@pytest.mark.asyncio
async def test_get_latest_corruption_data_returns_latest_entry(monkeypatch):
    import integrations.media_integrity_integration as module

    async def _sender(text, reply_markup):
        return None

    configure_sender(_sender)
    _corrupted_files_cache.clear()
    monkeypatch.setattr(module, "MAX_CORRUPTION_CACHE_ENTRIES", 5)

    first = MediaIntegrityWebhook(
        notification_type="CORRUPTION_DETECTED",
        summary_message="first",
        count=1,
        files=[CorruptedFileInfo(path="/a.mkv", size="1GB", error="err")],
    )
    second = MediaIntegrityWebhook(
        notification_type="CORRUPTION_DETECTED",
        summary_message="second",
        count=2,
        files=[CorruptedFileInfo(path="/b.mkv", size="2GB", error="err")],
    )

    await media_integrity_webhook(first, request=None)
    await media_integrity_webhook(second, request=None)

    latest = get_latest_corruption_data()
    assert latest is not None
    assert latest["count"] == 2


@pytest.mark.asyncio
async def test_media_corruption_callback_embeds_stable_cache_key(monkeypatch):
    import integrations.media_integrity_integration as module

    sent_messages = []

    async def _sender(text, reply_markup):
        sent_messages.append((text, reply_markup))

    configure_sender(_sender)
    _corrupted_files_cache.clear()
    monkeypatch.setattr(module, "MAX_CORRUPTION_CACHE_ENTRIES", 5)

    payload = MediaIntegrityWebhook(
        notification_type="CORRUPTION_DETECTED",
        summary_message="summary",
        count=7,
        files=[CorruptedFileInfo(path="/m/file.mkv", size="1GB", error="err")],
    )

    result = await media_integrity_webhook(payload, request=None)
    assert result["status"] == "ok"
    assert sent_messages

    reply_markup = sent_messages[0][1]
    button = reply_markup.inline_keyboard[0][0]
    assert button.callback_data.startswith(CORRUPTION_CALLBACK_ID_PREFIX)

    cache_key = button.callback_data[len(CORRUPTION_CALLBACK_ID_PREFIX) :]
    cached = get_corruption_data(cache_key)
    assert cached is not None
    assert cached["count"] == 7
