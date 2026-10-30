"""Tests for the requester tag normalization planner."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import requests  # noqa: E402
import retag_requesters  # noqa: E402
from retag_requesters import (  # noqa: E402
    build_overseerr_index,
    canonical_requested_label,
    plan_tag_merges,
)


def test_canonical_label_variants():
    assert canonical_requested_label("Orin_Requested") == "orin-requested"
    assert canonical_requested_label("orin requested") == "orin-requested"
    assert canonical_requested_label("overseer") is None
    assert canonical_requested_label("דנה-requested") is None


def test_plan_tag_merges_reuses_existing_canonical():
    tags = [
        {"id": 1, "label": "orin-requested"},
        {"id": 2, "label": "Orin-Requested"},
        {"id": 3, "label": "dana_requested"},
        {"id": 4, "label": "overseer"},
    ]
    assert plan_tag_merges(tags) == {
        2: (1, "orin-requested"),
        3: (None, "dana-requested"),
    }


def test_overseerr_index_orders_and_dedups():
    reqs = [
        {
            "createdAt": "2",
            "media": {"mediaType": "movie", "tmdbId": 5},
            "requestedBy": {"username": "B"},
        },
        {
            "createdAt": "1",
            "media": {"mediaType": "movie", "tmdbId": 5},
            "requestedBy": {"username": "A"},
        },
        {
            "createdAt": "3",
            "media": {"mediaType": "tv", "tvdbId": 9},
            "requestedBy": {"username": "A"},
        },
    ]
    assert build_overseerr_index(reqs, "tmdbId", "movie") == {5: ["a-requested", "b-requested"]}


def test_requester_cli_reports_upstream_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["retag_requesters.py", "--app", "radarr"])
    monkeypatch.setattr(
        retag_requesters,
        "load_bot_settings",
        lambda: {"overseerr_requester_tagging": {"source_tag_label": "overseerr"}},
    )
    monkeypatch.setattr(retag_requesters, "overseerr_api_key", lambda: "private-marker")

    def unavailable():
        raise requests.ConnectionError("https://service.invalid?apikey=private-marker")

    monkeypatch.setattr(retag_requesters, "overseerr_base_url", unavailable)

    assert retag_requesters.main() == 1
    error = capsys.readouterr().err
    assert "Requester backfill failed (ConnectionError)" in error
    assert "private-marker" not in error
    assert "Traceback" not in error
