"""Tests for the Plex-username to tag-name map and its CLI planning."""

from __future__ import annotations

import sys
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
