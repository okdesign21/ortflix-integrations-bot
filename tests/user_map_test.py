"""Tests for the Plex-username to tag-name map and its CLI planning."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import map_users  # noqa: E402
import user_map  # noqa: E402

OVERSEERR = [
    {"displayName": "レゼ", "username": "レゼ", "email": "a@x.com", "plexUsername": "legal94"},
    {"displayName": "dana", "email": "dana@x.com"},
]


@pytest.fixture(autouse=True)
def map_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORTFLIX_USER_MAP_DIR", str(tmp_path))
    return tmp_path


def test_propose_tag_matches_plex_name_then_email_then_self():
    assert user_map.propose_tag("legal94", "", OVERSEERR) == "a"
    assert user_map.propose_tag("DanaPlex", "Dana@x.com", OVERSEERR) == "dana"
    assert user_map.propose_tag("stranger", "s@x.com", OVERSEERR) == "stranger"


def test_save_and_load_roundtrip():
    user_map.save_user_map({"chen": {"tag": "chencoh2", "manual": True}})
    assert user_map.load_user_map() == {"chen": {"tag": "chencoh2", "manual": True}}


def test_resolve_uses_map_without_network(monkeypatch):
    user_map.save_user_map({"chen": {"tag": "chencoh2", "manual": True}})
    monkeypatch.setattr(user_map, "fetch_tautulli_users", lambda: pytest.fail("no lookup"))
    assert user_map.resolve_user("Chen") == "chencoh2"


def test_resolve_adds_missing_user(monkeypatch):
    monkeypatch.setattr(user_map, "fetch_tautulli_users", lambda: [{"username": "legal94"}])
    monkeypatch.setattr(user_map, "fetch_overseerr_users", lambda: OVERSEERR)
    assert user_map.resolve_user("legal94") == "a"
    assert user_map.load_user_map() == {"legal94": {"tag": "a", "manual": False}}


def test_resolve_dry_run_does_not_save(monkeypatch):
    monkeypatch.setattr(user_map, "fetch_tautulli_users", lambda: [])
    monkeypatch.setattr(user_map, "fetch_overseerr_users", lambda: [])
    assert user_map.resolve_user("newbie", persist=False) == "newbie"
    assert user_map.load_user_map() == {}


def test_resolve_lookup_failure_not_saved(monkeypatch):
    def boom():
        raise ValueError("down")

    monkeypatch.setattr(user_map, "fetch_tautulli_users", boom)
    assert user_map.resolve_user("someone") == "someone"
    assert user_map.load_user_map() == {}


def test_non_ascii_user_is_unknown():
    assert user_map.resolve_user("レゼ") == "unknown"


def test_username_identity_preserves_punctuation_and_normalizes_case():
    assert user_map.user_key("Sam_One") == "sam_one"
    assert user_map.user_key("SAM-ONE") == "sam-one"
    assert user_map.user_key("sam_one") != user_map.user_key("sam-one")


def test_distinct_punctuated_usernames_keep_separate_map_entries():
    user_map.save_user_map(
        {
            "sam_one": {"tag": "underscore-user", "manual": True},
            "sam-one": {"tag": "hyphen-user", "manual": True},
        }
    )

    assert user_map.resolve_user("Sam_One") == "underscore-user"
    assert user_map.resolve_user("sam-one") == "hyphen-user"


@pytest.mark.parametrize(
    "entry",
    [
        "chencoh2",
        {"tag": 42},
        {"tag": "chen", "manual": "yes"},
        {"tag": "chen", "nickname": ["Chen"]},
    ],
)
def test_load_rejects_malformed_user_entries(map_dir, entry):
    path = map_dir / user_map.MAP_FILE
    path.write_text(json.dumps({"users": {"chen": entry}}), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid user map"):
        user_map.load_user_map(path)


def test_load_rejects_case_insensitive_duplicate_identities(map_dir):
    path = map_dir / user_map.MAP_FILE
    path.write_text(
        '{"users":{"Chen":{"tag":"first"},"chen":{"tag":"second"}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate username identity"):
        user_map.load_user_map(path)


def test_map_cli_reports_invalid_map_without_traceback(map_dir, monkeypatch, capsys):
    (map_dir / user_map.MAP_FILE).write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["map_users.py", "show"])

    assert map_users.main() == 1
    assert "Map command failed (ValueError)" in capsys.readouterr().err


def test_map_cli_reports_lock_failure_without_traceback(monkeypatch, capsys):
    @contextmanager
    def denied_lock():
        raise PermissionError("private path")
        yield

    monkeypatch.setattr(map_users, "map_lock", denied_lock)
    monkeypatch.setattr(sys, "argv", ["map_users.py", "set", "chen", "chen", "--apply"])

    assert map_users.main() == 1
    error = capsys.readouterr().err
    assert "Map command failed (PermissionError)" in error
    assert "private path" not in error


def test_sync_keeps_locked_and_updates_auto():
    entries = {
        "chen": {"tag": "chencoh2", "manual": True},
        "legal94": {"tag": "legal94", "manual": False},
    }
    tautulli = [
        {"username": "Chen", "email": "a@x.com"},
        {"username": "legal94"},
        {"username": "new"},
    ]
    result, changes = map_users.plan_sync(entries, tautulli, OVERSEERR)
    assert result["chen"] == {"tag": "chencoh2", "manual": True}
    assert result["legal94"] == {"tag": "a", "manual": False}
    assert result["new"] == {"tag": "new", "manual": False}
    assert sorted(changes) == ["+ new -> new", "~ legal94: legal94 -> a"]


def test_nickname_survives_roundtrip_and_sync():
    entries = {"legal94": {"tag": "legal94", "manual": False, "nickname": "Legal"}}
    user_map.save_user_map(entries)
    loaded = user_map.load_user_map()
    assert loaded["legal94"]["nickname"] == "Legal"
    result, _ = map_users.plan_sync(loaded, [{"username": "legal94"}], OVERSEERR)
    assert result["legal94"] == {"tag": "a", "manual": False, "nickname": "Legal"}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise user_map.requests.HTTPError(
                f"{self.status_code} for url: http://t/api/v2?apikey=SECRET", response=self
            )

    def json(self):
        return self._body


def test_tautulli_http_error_hides_api_key(monkeypatch):
    monkeypatch.setattr(user_map.requests, "get", lambda *a, **k: FakeResponse(500))
    with pytest.raises(user_map.TautulliError) as exc:
        user_map.tautulli_call("http://t", "SECRET", "get_users")
    assert "SECRET" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_tautulli_transport_error_hides_api_key(monkeypatch):
    def boom(*a, **k):
        raise user_map.requests.ConnectionError("http://t/api/v2?apikey=SECRET")

    monkeypatch.setattr(user_map.requests, "get", boom)
    with pytest.raises(user_map.TautulliError) as exc:
        user_map.tautulli_call("http://t", "SECRET", "get_users")
    assert "SECRET" not in str(exc.value)


def test_tautulli_result_error_is_recoverable_type(monkeypatch):
    body = {"response": {"result": "error", "message": "no item", "data": {}}}
    monkeypatch.setattr(user_map.requests, "get", lambda *a, **k: FakeResponse(200, body))
    with pytest.raises(user_map.TautulliResultError):
        user_map.tautulli_call("http://t", "k", "get_metadata")


def test_resolve_tautulli_error_response_is_not_saved(monkeypatch):
    monkeypatch.setenv("TAUTULLI_URL", "http://t")
    monkeypatch.setenv("TAUTULLI_API_KEY", "k")
    body = {"response": {"result": "error", "message": "bad", "data": {}}}
    monkeypatch.setattr(user_map.requests, "get", lambda *a, **k: FakeResponse(200, body))
    assert user_map.resolve_user("someone") == "someone"
    assert user_map.load_user_map() == {}


def test_missing_overseerr_key_is_recoverable(monkeypatch):
    monkeypatch.delenv("OVERSEERR_API_KEY", raising=False)
    monkeypatch.delenv("SEERR_API_KEY", raising=False)
    monkeypatch.setattr(user_map, "fetch_tautulli_users", lambda: [])
    assert user_map.resolve_user("someone") == "someone"
    assert user_map.load_user_map() == {}


def test_unreadable_map_falls_back_without_overwriting(map_dir):
    bad = map_dir / user_map.MAP_FILE
    bad.write_text("{not json", encoding="utf-8")
    assert user_map.resolve_user("Chen") == "chen"
    assert bad.read_text(encoding="utf-8") == "{not json"


def test_save_failure_falls_back(monkeypatch):
    monkeypatch.setattr(user_map, "fetch_tautulli_users", lambda: [])
    monkeypatch.setattr(user_map, "fetch_overseerr_users", lambda: [])

    def deny(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(user_map, "save_user_map", deny)
    assert user_map.resolve_user("newbie") == "newbie"
