"""Tests for the requester tag normalization planner."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
