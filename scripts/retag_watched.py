#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retag_watched.py — Backfill "<user>-watched" (+ triage) tags in Radarr/Sonarr from Tautulli history.

A movie counts as watched when a user fully watched it; a series counts when a user fully
watched at least one of its episodes. Plex rating keys are resolved to TMDB (movies) and
TVDB (shows) ids through Tautulli metadata and matched against Radarr/Sonarr.

Dry-run by default; pass --apply to write changes.

  python scripts/retag_watched.py [--apply] [--app radarr|sonarr|both]

Env: TAUTULLI_URL, TAUTULLI_API_KEY, RADARR_API_KEY, SONARR_API_KEY (+ *_URL overrides).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import requests
from integration_utils import (
    _clean_username,
    fail,
    load_bot_settings,
    radarr_base_url,
    radarr_session,
    sonarr_base_url,
    sonarr_session,
)
from retag_requesters import bulk_edit, ensure_tag_id, get_json

HISTORY_PAGE_SIZE = 1000

APPS = {
    "radarr": {
        "base_url": radarr_base_url,
        "session": radarr_session,
        "api_key_env": "RADARR_API_KEY",
        "list_path": "/api/v3/movie",
        "editor_path": "/api/v3/movie/editor",
        "ids_field": "movieIds",
        "history_media_type": "movie",
        "item_id_field": "tmdbId",
        "guid_prefix": "tmdb://",
    },
    "sonarr": {
        "base_url": sonarr_base_url,
        "session": sonarr_session,
        "api_key_env": "SONARR_API_KEY",
        "list_path": "/api/v3/series",
        "editor_path": "/api/v3/series/editor",
        "ids_field": "seriesIds",
        "history_media_type": "episode",
        "item_id_field": "tvdbId",
        "guid_prefix": "tvdb://",
    },
}


def watched_label(username: str, suffix: str) -> str:
    return f"{_clean_username(username) or 'unknown'}-{suffix}"


def tautulli_call(base: str, key: str, cmd: str, **params):
    r = requests.get(
        f"{base.rstrip('/')}/api/v2",
        params={"apikey": key, "cmd": cmd, **params},
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()["response"]
    if body.get("result") != "success":
        fail(f"Tautulli {cmd} failed: {body.get('message')}")
    return body["data"]


def fetch_watched(base: str, key: str, media_type: str) -> dict[str, set]:
    """Return {plex rating key of movie/show: {usernames who fully watched it}}."""
    watched: dict[str, set] = defaultdict(set)
    start = 0
    while True:
        data = tautulli_call(
            base,
            key,
            "get_history",
            media_type=media_type,
            start=start,
            length=HISTORY_PAGE_SIZE,
        )
        rows = data["data"]
        for row in rows:
            if float(row.get("watched_status") or 0) < 1:
                continue
            rating_key = (
                row["grandparent_rating_key"] if media_type == "episode" else row["rating_key"]
            )
            if rating_key and row.get("user"):
                watched[str(rating_key)].add(row["user"])
        start += len(rows)
        if not rows or start >= data["recordsFiltered"]:
            return watched


def external_id(base: str, key: str, rating_key: str, prefix: str) -> int | None:
    """Return the external id for a Plex item, or None if it no longer exists in Plex."""
    try:
        data = tautulli_call(base, key, "get_metadata", rating_key=rating_key)
    except requests.HTTPError:
        return None
    for guid in data.get("guids") or []:
        if guid.startswith(prefix):
            value = guid[len(prefix) :]
            return int(value) if value.isdigit() else None
    return None


def users_by_external_id(t_base, t_key, cfg) -> dict[int, set]:
    watched = fetch_watched(t_base, t_key, cfg["history_media_type"])
    users_by_external: dict[int, set] = defaultdict(set)
    for rating_key, users in watched.items():
        ext = external_id(t_base, t_key, rating_key, cfg["guid_prefix"])
        if ext is not None:
            users_by_external[ext] |= users
    print(f"  Tautulli: {len(watched)} watched titles ({len(users_by_external)} resolvable)")
    return users_by_external


def plan_tags(items, users_by_external, label_ids, suffix, id_field) -> dict[str, list[int]]:
    to_tag: dict[str, list[int]] = defaultdict(list)
    for item in items:
        for user in users_by_external.get(item.get(id_field), ()):
            label = watched_label(user, suffix)
            if label_ids.get(label) not in item.get("tags", []):
                to_tag[label].append(item["id"])
    return to_tag


def backfill_app(name, cfg, t_base, t_key, suffix, triage_label, apply: bool) -> None:
    base, session = cfg["base_url"](), cfg["session"](os.environ[cfg["api_key_env"]])
    print(f"== {name} ({base})")
    users = users_by_external_id(t_base, t_key, cfg)
    label_ids = {t["label"]: t["id"] for t in get_json(session, base, "/api/v3/tag")}
    items = get_json(session, base, cfg["list_path"])
    to_tag = plan_tags(items, users, label_ids, suffix, cfg["item_id_field"])

    triage_ids: set[int] = set()
    for label, ids in sorted(to_tag.items()):
        print(f"  [watched] '{label}': {len(ids)} items to tag")
        triage_ids.update(ids)
        if apply:
            tag_id = ensure_tag_id(session, base, label, apply, label_ids)
            bulk_edit(session, base, cfg, ids, [tag_id], "add")
    if triage_ids:
        print(f"  [watched] '{triage_label}': {len(triage_ids)} items")
        if apply:
            triage_id = ensure_tag_id(session, base, triage_label, apply, label_ids)
            bulk_edit(session, base, cfg, sorted(triage_ids), [triage_id], "add")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--app", choices=["radarr", "sonarr", "both"], default="both")
    args = ap.parse_args()

    t_base, t_key = os.getenv("TAUTULLI_URL"), os.getenv("TAUTULLI_API_KEY")
    if not t_base or not t_key:
        fail("Missing TAUTULLI_URL or TAUTULLI_API_KEY in environment")
    for name in ("radarr", "sonarr") if args.app == "both" else (args.app,):
        if not os.getenv(APPS[name]["api_key_env"]):
            fail(f"Missing {APPS[name]['api_key_env']} in environment")

    settings = load_bot_settings()["tautulli_watched"]
    suffix = str(settings["watched_tag_suffix"]).strip().lower() or "watched"
    triage_label = str(settings["triage_tag_label"]).strip() or "tbd"
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    for name in ("radarr", "sonarr") if args.app == "both" else (args.app,):
        backfill_app(name, APPS[name], t_base, t_key, suffix, triage_label, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
