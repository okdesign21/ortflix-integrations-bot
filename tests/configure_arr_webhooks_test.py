"""Tests for the declarative Radarr/Sonarr webhook configuration."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import configure_arr_webhooks as cfg  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402

ENTRY = {
    "name": "Webhook",
    "url": "http://integrations-bot:7777/api/v1/webhooks/radarr",
    "method": "POST",
    "events": ["onGrab", "onDownload"],
}


def connection(**overrides):
    conn = {
        "id": 4,
        "name": "Webhook",
        "implementation": "Webhook",
        "onGrab": True,
        "supportsOnGrab": True,
        "onDownload": True,
        "supportsOnDownload": True,
        "onRename": False,
        "supportsOnRename": True,
        "fields": [
            {"name": "url", "value": ENTRY["url"]},
            {"name": "method", "value": 1},
            {"name": "headers", "value": []},
        ],
    }
    conn.update(overrides)
    return conn


class FakeSession:
    def __init__(self, existing):
        self.existing = existing
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        body = {"GET": self.existing}.get(method)
        if url.endswith("/schema"):
            body = [connection(id=0, name="")]
        return FakeResponse(body)


class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.content = b"x" if body is not None else b""

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def run_sync(monkeypatch, existing, apply, token=None, entry=ENTRY):
    session = FakeSession(existing)
    monkeypatch.setitem(cfg.APPS["radarr"], "base_url", lambda: "http://radarr")
    monkeypatch.setitem(cfg.APPS["radarr"], "session", lambda key: session)
    monkeypatch.setattr(cfg, "_env", lambda name, required=False, default=None: "key")
    pending = cfg.sync_app("radarr", [entry], token, apply)
    return pending, session.calls


def test_matching_connection_is_a_noop(monkeypatch):
    pending, calls = run_sync(monkeypatch, [connection()], apply=True)
    assert pending == 0
    assert [c[0] for c in calls] == ["GET"]


def test_dry_run_reports_drift_without_writing(monkeypatch, capsys):
    drifted = connection(onRename=True)
    pending, calls = run_sync(monkeypatch, [drifted], apply=False)
    assert pending == 1
    assert [c[0] for c in calls] == ["GET"]
    assert "onRename: True -> False" in capsys.readouterr().out


def test_apply_updates_existing_connection(monkeypatch):
    pending, calls = run_sync(monkeypatch, [connection(onRename=True)], apply=True)
    method, url, body = calls[-1]
    assert (pending, method, url) == (1, "PUT", "http://radarr/api/v3/notification/4")
    assert body["onRename"] is False


def test_apply_creates_missing_connection_from_schema(monkeypatch):
    pending, calls = run_sync(monkeypatch, [], apply=True)
    method, url, body = calls[-1]
    assert (pending, method, url) == (1, "POST", "http://radarr/api/v3/notification")
    assert body["name"] == "Webhook" and body["onGrab"] is True


def test_token_header_is_configured_but_never_printed(monkeypatch, capsys):
    pending, calls = run_sync(monkeypatch, [connection()], apply=True, token="s3cret")
    assert pending == 1
    body = calls[-1][2]
    assert cfg.get_field(body, "headers") == [{"key": "x-webhook-token", "value": "s3cret"}]
    assert "s3cret" not in capsys.readouterr().out


def test_other_connections_are_untouched(monkeypatch):
    other = connection(id=9, name="Telegram", implementation="Telegram", onRename=True)
    _, calls = run_sync(monkeypatch, [connection(), other], apply=True)
    assert all(c[0] == "GET" for c in calls)


def test_unsupported_event_fails(monkeypatch):
    entry = {**ENTRY, "events": ["onNope"]}
    with pytest.raises(SystemExit):
        run_sync(monkeypatch, [connection()], apply=False, entry=entry)


def test_duplicate_names_fail(monkeypatch):
    with pytest.raises(SystemExit):
        run_sync(monkeypatch, [connection(), connection(id=5)], apply=False)


def test_non_webhook_with_same_name_fails(monkeypatch):
    with pytest.raises(SystemExit):
        run_sync(monkeypatch, [connection(implementation="Telegram")], apply=False)


def test_api_failure_is_sanitized(capsys):
    class Boom:
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("http://radarr/?apikey=topsecret")

    with pytest.raises(SystemExit):
        cfg.api(Boom(), "GET", "http://radarr/api/v3/notification")
    assert "topsecret" not in capsys.readouterr().err


def test_bundled_config_is_valid():
    desired = cfg.load_desired(cfg.DEFAULT_CONFIG)
    assert set(desired) == {"radarr", "sonarr"}
