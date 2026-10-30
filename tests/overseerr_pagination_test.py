"""Tests for paging through Overseerr requests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from integration_utils import find_overseerr_request  # noqa: E402


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.skips = []

    def get(self, url, params=None, timeout=None):
        self.skips.append(params["skip"])
        results = self.pages.get(params["skip"], [])
        return type(
            "R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"results": results}}
        )()


def request(tmdb_id):
    return {"id": tmdb_id, "media": {"tmdbId": tmdb_id, "mediaType": "movie"}}


def test_finds_request_on_later_page():
    session = FakeSession({0: [request(1), request(2)], 2: [request(3)]})
    found = find_overseerr_request(session, "http://o", {"take": 2, "skip": 0}, tmdb_id=3)
    assert found["id"] == 3
    assert session.skips == [0, 2]


def test_returns_none_after_last_page():
    session = FakeSession({0: [request(1), request(2)], 2: [request(3)]})
    assert find_overseerr_request(session, "http://o", {"take": 2}, tmdb_id=9) is None
