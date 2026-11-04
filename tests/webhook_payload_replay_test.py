"""Replay sanitized webhook samples through the HTTP endpoints."""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

WEBHOOK_TOKEN = "sample-webhook-token"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "webhooks"
HEADERS = {"x-webhook-token": WEBHOOK_TOKEN}


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_replay_sample_webhooks(monkeypatch, caplog):
    import bot
    import integrations.media_integrity_integration as media_integrity
    import integrations.overseerr_integration as overseerr
    import integrations.radarr_automation as radarr
    import integrations.sonarr_automation as sonarr
    import integrations.tautulli_automation as tautulli

    for module in (media_integrity, overseerr, radarr, sonarr, tautulli):
        monkeypatch.setattr(module, "_webhook_token_getter", lambda: WEBHOOK_TOKEN)

    send_overseerr = AsyncMock()
    send_media_integrity = AsyncMock()
    execute_radarr = AsyncMock()
    execute_sonarr = AsyncMock()
    execute_tautulli = AsyncMock()
    monkeypatch.setattr(overseerr, "_sender", send_overseerr)
    monkeypatch.setattr(media_integrity, "_send_text", send_media_integrity)
    monkeypatch.setattr(radarr, "_execute_radarr_script", execute_radarr)
    monkeypatch.setattr(sonarr, "_execute_sonarr_script", execute_sonarr)
    monkeypatch.setattr(tautulli, "_execute_tautulli_script", execute_tautulli)

    async def passthrough(payload):
        return payload

    monkeypatch.setattr(overseerr, "_enrich_payload_with_seerr_request", passthrough)
    monkeypatch.setattr(overseerr, "_enrich_media_available_from_radarr", passthrough)

    cases = (
        ("/api/v1/webhooks/overseerr", "overseerr_media_pending.json"),
        ("/api/v1/webhooks/media-check", "media_integrity_corruption.json"),
        ("/api/v1/webhooks/radarr", "radarr_download.json"),
        ("/api/v1/webhooks/sonarr", "sonarr_download.json"),
        ("/api/v1/webhooks/tautulli", "tautulli_watched.json"),
    )

    caplog.set_level(logging.INFO)
    async with AsyncClient(
        transport=ASGITransport(app=bot.app),
        base_url="http://test",
    ) as client:
        for path, fixture_name in cases:
            response = await client.post(
                path,
                json=_read_fixture(fixture_name),
                headers=HEADERS,
            )
            assert response.status_code == 200, (path, response.text)
            assert response.headers["X-Request-ID"]

    send_overseerr.assert_awaited_once()
    send_media_integrity.assert_awaited_once()
    assert execute_radarr.await_count == 2
    assert execute_sonarr.await_count == 2
    execute_tautulli.assert_awaited_once()
    assert execute_radarr.await_args_list[0].args[1]["customMetadata"] == {"source": "fixture"}

    webhook_records = [
        record for record in caplog.records if getattr(record, "event", "").startswith("webhook.")
    ]
    assert webhook_records
    assert all(getattr(record, "request_id", None) for record in webhook_records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/webhooks/overseerr", {"notification_type": []}),
        (
            "/api/v1/webhooks/media-check",
            {"notification_type": "CORRUPTION_DETECTED", "count": "invalid"},
        ),
        ("/api/v1/webhooks/radarr", {"eventType": [], "movie": {}}),
        ("/api/v1/webhooks/sonarr", {"eventType": "Download", "series": []}),
        ("/api/v1/webhooks/tautulli", {"event": [], "username": "sample-user"}),
    ],
)
async def test_automation_webhooks_reject_invalid_typed_fields(monkeypatch, path, payload):
    import bot
    import integrations.media_integrity_integration as media_integrity
    import integrations.overseerr_integration as overseerr
    import integrations.radarr_automation as radarr
    import integrations.sonarr_automation as sonarr
    import integrations.tautulli_automation as tautulli

    for module in (media_integrity, overseerr, radarr, sonarr, tautulli):
        monkeypatch.setattr(module, "_webhook_token_getter", lambda: WEBHOOK_TOKEN)

    async with AsyncClient(
        transport=ASGITransport(app=bot.app),
        base_url="http://test",
    ) as client:
        response = await client.post(path, json=payload, headers=HEADERS)

    assert response.status_code == 422
