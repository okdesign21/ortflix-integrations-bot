"""Tests for the watched-tag backfill helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import retag_watched  # noqa: E402
from user_map import TautulliError, TautulliResultError  # noqa: E402


def test_missing_metadata_is_skipped(monkeypatch):
    def missing(*a, **k):
        raise TautulliResultError("no item")

    monkeypatch.setattr(retag_watched, "tautulli_call", missing)
    assert retag_watched.external_id("http://t", "k", "1", "tmdb://") is None


def test_metadata_transport_failure_stays_fatal(monkeypatch):
    def down(*a, **k):
        raise TautulliError("HTTP 401")

    monkeypatch.setattr(retag_watched, "tautulli_call", down)
    try:
        retag_watched.external_id("http://t", "k", "1", "tmdb://")
    except TautulliError:
        return
    raise AssertionError("expected TautulliError")


def test_plan_tags_resolves_each_user_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        retag_watched,
        "resolve_user",
        lambda user, persist: calls.append(user) or user,
    )
    items = [{"id": i, "tmdbId": i, "tags": []} for i in (1, 2, 3)]
    users = {1: {"chen"}, 2: {"chen"}, 3: {"chen"}}
    plan = retag_watched.plan_tags(items, users, {}, "watched", "tmdbId")
    assert calls == ["chen"]
    assert plan == {"chen-watched": [1, 2, 3]}


def test_watched_cli_reports_service_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["retag_watched.py", "--app", "radarr"])
    monkeypatch.setenv("TAUTULLI_URL", "http://tautulli")
    monkeypatch.setenv("TAUTULLI_API_KEY", "private-marker")
    monkeypatch.setenv("RADARR_API_KEY", "private-radarr")
    monkeypatch.setattr(
        retag_watched,
        "load_bot_settings",
        lambda: {
            "tautulli_watched": {
                "watched_tag_suffix": "watched",
                "triage_tag_label": "tbd",
            }
        },
    )

    def unavailable(*args, **kwargs):
        raise requests.Timeout("private-marker")

    monkeypatch.setattr(retag_watched, "backfill_app", unavailable)

    assert retag_watched.main() == 1
    error = capsys.readouterr().err
    assert "Watched backfill failed (Timeout)" in error
    assert "private-marker" not in error
    assert "Traceback" not in error
