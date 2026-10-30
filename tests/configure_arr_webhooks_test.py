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
    assert set(desired) == {"radarr", "sonarr", "seerr", "tautulli"}


def test_url_values_are_not_printed_on_update_or_create(monkeypatch, capsys):
    secret_url = "http://bot/hook?token=urlsecret"
    entry = {**ENTRY, "url": secret_url}
    run_sync(monkeypatch, [connection()], apply=False, entry=entry)
    run_sync(monkeypatch, [], apply=False, entry=entry)
    out = capsys.readouterr().out
    assert "urlsecret" not in out and "url: changed" in out


def test_unreadable_config_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit):
        cfg.load_desired(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SystemExit):
        cfg.load_desired(bad)


@pytest.mark.parametrize("override", [{"name": ["Webhook"]}, {"url": {"a": "b"}}])
def test_non_string_name_or_url_is_rejected(tmp_path, override):
    entry = {**ENTRY, **override}
    path = tmp_path / "c.yaml"
    path.write_text(cfg.yaml.safe_dump({"radarr": [entry]}))
    with pytest.raises(SystemExit):
        cfg.load_desired(path)


def test_event_with_false_support_flag_is_rejected(monkeypatch):
    unsupported = connection(onGrab=False, supportsOnGrab=False)
    with pytest.raises(SystemExit):
        run_sync(monkeypatch, [unsupported], apply=False)


def test_non_json_response_is_a_clean_error(capsys):
    class Html:
        content = b"<html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("no json")

    class Session:
        def request(self, *args, **kwargs):
            return Html()

    with pytest.raises(SystemExit):
        cfg.api(Session(), "GET", "http://radarr/api/v3/notification")
    assert "non-JSON" in capsys.readouterr().err


def test_apps_without_declarations_are_skipped(monkeypatch, tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(cfg.yaml.safe_dump({"radarr": [ENTRY]}))
    called = []
    monkeypatch.setattr(cfg, "sync_app", lambda app, *a: called.append(app) or 0)
    monkeypatch.setattr(cfg, "_env", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["x", "--config", str(path)])
    assert cfg.main() == 0
    assert called == ["radarr"]


SEERR_ENTRY = {
    "enabled": True,
    "embed_poster": True,
    "url": "http://integrations-bot:7777/api/v1/webhooks/overseerr",
    "events": ["MEDIA_PENDING", "ISSUE_CREATED"],
}


def seerr_state(**overrides):
    state = {
        "enabled": True,
        "embedPoster": True,
        "types": 2 + 256,
        "options": {
            "webhookUrl": SEERR_ENTRY["url"],
            "customHeaders": [],
            "authHeader": "keep",
        },
    }
    state.update(overrides)
    return state


def run_seerr(monkeypatch, state, apply, entry=SEERR_ENTRY, token=None):
    session = FakeSession(state)
    monkeypatch.setattr(cfg, "overseerr_base_url", lambda: "http://seerr")
    monkeypatch.setattr(cfg, "overseerr_api_key", lambda: "key")
    monkeypatch.setattr(cfg, "overseerr_session", lambda key: session)
    pending = cfg.sync_seerr(entry, token, apply)
    return pending, session.calls


def test_seerr_matching_state_is_a_noop(monkeypatch):
    pending, calls = run_seerr(monkeypatch, seerr_state(), apply=True)
    assert pending == 0 and [c[0] for c in calls] == ["GET"]


def test_seerr_drift_is_applied_and_preserves_other_options(monkeypatch, capsys):
    pending, calls = run_seerr(monkeypatch, seerr_state(types=2), apply=True)
    method, url, body = calls[-1]
    assert (pending, method, url) == (
        1,
        "POST",
        "http://seerr/api/v1/settings/notifications/webhook",
    )
    assert body["types"] == 258 and body["options"]["authHeader"] == "keep"
    assert "types: 2 -> 258" in capsys.readouterr().out


def test_seerr_payload_is_left_untouched(monkeypatch):
    state = seerr_state(types=2)
    state["options"]["jsonPayload"] = "live-default"
    _, calls = run_seerr(monkeypatch, state, apply=True)
    assert calls[-1][2]["options"]["jsonPayload"] == "live-default"


def test_seerr_payload_file_setting_is_rejected():
    with pytest.raises(SystemExit):
        cfg.validate_seerr({**SEERR_ENTRY, "json_payload_file": "p.json"})


def test_seerr_unknown_event_is_rejected():
    with pytest.raises(SystemExit):
        cfg.validate_seerr({**SEERR_ENTRY, "events": ["NOPE"]})


def test_seerr_token_header_not_printed(monkeypatch, capsys):
    _, calls = run_seerr(monkeypatch, seerr_state(), apply=True, token="s3cret")
    assert calls[-1][2]["options"]["customHeaders"] == [
        {"key": "x-webhook-token", "value": "s3cret"}
    ]
    assert "s3cret" not in capsys.readouterr().out


TAUTULLI_ENTRY = {
    "name": "Bot",
    "url": "http://integrations-bot:7777/api/v1/webhooks/tautulli",
    "method": "POST",
    "events": ["on_watched"],
}


def tautulli_state(**overrides):
    state = {
        "id": 7,
        "agent_id": 25,
        "friendly_name": "Bot",
        "config": {"hook": TAUTULLI_ENTRY["url"], "method": "POST"},
        "actions": {"on_watched": "1", "on_play": "0"},
        "notify_text": {"on_watched": {"subject": "", "body": ""}},
        "custom_conditions": [],
    }
    state.update(overrides)
    return state


class FakeTautulli:
    """Records Tautulli API calls; serves canned notifier data by command."""

    def __init__(self, notifiers, config):
        self.notifiers = notifiers
        self.config = config
        self.calls = []

    def request(self, method, url, data=None, **kwargs):
        self.calls.append(dict(data))
        cmd = data["cmd"]
        if cmd == "get_notifiers":
            payload = self.notifiers
        elif cmd == "get_notifier_config":
            payload = self.config
        else:
            payload = None
        if cmd == "add_notifier_config":
            self.notifiers = [*self.notifiers, {"id": 9, "agent_id": 25, "friendly_name": ""}]
        return FakeResponse({"response": {"result": "success", "message": None, "data": payload}})


def run_tautulli(monkeypatch, notifiers, config, apply, entry=TAUTULLI_ENTRY, token=None):
    fake = FakeTautulli(notifiers, config)
    monkeypatch.setattr(cfg.requests, "Session", lambda: _with_headers(fake))
    monkeypatch.setattr(cfg, "_env", lambda *a, **k: "tkey")
    monkeypatch.setenv("TAUTULLI_URL", "http://tautulli:8181")
    pending = cfg.sync_tautulli([entry], token, apply, Path("c.yaml"))
    return pending, fake.calls


def _with_headers(fake):
    fake.headers = {}
    return fake


def test_tautulli_matching_notifier_is_a_noop(monkeypatch):
    notifiers = [{"id": 7, "agent_id": 25, "friendly_name": "Bot"}]
    pending, calls = run_tautulli(monkeypatch, notifiers, tautulli_state(), apply=True)
    assert pending == 0
    assert {c["cmd"] for c in calls} == {"get_notifiers", "get_notifier_config"}


def test_tautulli_drift_applies_trigger_url_and_token_without_printing(monkeypatch, capsys):
    notifiers = [{"id": 7, "agent_id": 25, "friendly_name": "Bot"}]
    state = tautulli_state(actions={"on_watched": "0", "on_play": "1"})
    pending, calls = run_tautulli(monkeypatch, notifiers, state, apply=True, token="s3cret")
    sent = calls[-1]
    assert pending == 1 and sent["cmd"] == "set_notifier_config" and sent["notifier_id"] == 7
    assert (sent["on_watched"], sent["on_play"]) == (1, 0)
    assert json_loads(sent["on_watched_subject"]) == {"x-webhook-token": "s3cret"}
    assert "s3cret" not in capsys.readouterr().out


def json_loads(text):
    return cfg.json.loads(text)


def test_tautulli_dry_run_does_not_write(monkeypatch):
    notifiers = [{"id": 7, "agent_id": 25, "friendly_name": "Bot"}]
    state = tautulli_state(actions={"on_watched": "0", "on_play": "0"})
    pending, calls = run_tautulli(monkeypatch, notifiers, state, apply=False)
    assert pending == 1 and "set_notifier_config" not in {c["cmd"] for c in calls}


def test_tautulli_creates_missing_notifier(monkeypatch):
    pending, calls = run_tautulli(monkeypatch, [], tautulli_state(id=9), apply=True)
    cmds = [c["cmd"] for c in calls]
    assert pending == 1
    assert "add_notifier_config" in cmds and cmds[-1] == "set_notifier_config"
    assert calls[-1]["notifier_id"] == 9 and calls[-1]["webhook_hook"] == TAUTULLI_ENTRY["url"]


def test_tautulli_non_webhook_with_same_name_fails(monkeypatch):
    notifiers = [{"id": 4, "agent_id": 15, "friendly_name": "Bot"}]
    with pytest.raises(SystemExit):
        run_tautulli(monkeypatch, notifiers, tautulli_state(), apply=False)


def test_tautulli_unsupported_event_fails(monkeypatch):
    notifiers = [{"id": 7, "agent_id": 25, "friendly_name": "Bot"}]
    entry = {**TAUTULLI_ENTRY, "events": ["on_nope"]}
    with pytest.raises(SystemExit):
        run_tautulli(monkeypatch, notifiers, tautulli_state(), apply=False, entry=entry)


def test_tautulli_error_response_is_a_clean_failure(capsys):
    class Err:
        def request(self, *args, **kwargs):
            return FakeResponse({"response": {"result": "error", "message": "Invalid apikey"}})

    with pytest.raises(SystemExit):
        cfg.tautulli_call(Err(), "http://t", "k", "get_notifiers")
    assert "Invalid apikey" in capsys.readouterr().err


def test_tautulli_body_template_and_conditions_are_compared(monkeypatch, tmp_path):
    (tmp_path / "b.json").write_text('{"event": "watched"}')
    entry = {
        **TAUTULLI_ENTRY,
        "json_data_file": "b.json",
        "custom_conditions": [{"parameter": "media_type", "operator": "is", "value": ["movie"]}],
    }
    state = tautulli_state()
    wanted = cfg.tautulli_wanted(state, entry, None, tmp_path / "c.yaml")
    changes = cfg.tautulli_changes(state, wanted, entry)
    assert "on_watched JSON data: changed" in changes and "custom_conditions: changed" in changes
    state["notify_text"]["on_watched"]["body"] = '{ "event":"watched" }'
    state["custom_conditions"] = entry["custom_conditions"]
    assert cfg.tautulli_changes(state, wanted, entry) == []


@pytest.mark.parametrize(
    "bad", [{"events": ["watched"]}, {"method": "FETCH"}, {"custom_conditions": "x"}, {"name": 5}]
)
def test_tautulli_config_validation(bad):
    with pytest.raises(SystemExit):
        cfg.validate_tautulli([{**TAUTULLI_ENTRY, **bad}])
