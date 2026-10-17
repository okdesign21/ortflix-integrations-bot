#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retag_requesters.py — Normalize and backfill "<user>-requested" tags in Radarr/Sonarr.

Phase 1 (normalize): merges requester tag variants (case, spacing, punctuation) into
the canonical slug label, moves items onto it, then deletes the duplicate tags.
Phase 2 (backfill): items with no requester tag get one from the matching Overseerr
request, plus the Overseerr source tag. Items with no request (pre-Overseerr or manually
added) get "okdesign-requested".

Dry-run by default; pass --apply to write changes.

  python scripts/retag_requesters.py [--apply] [--app radarr|sonarr|both]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

from integration_utils import (
    DEFAULT_REQUESTER_LABEL,
    _clean_username,
    fail,
    load_bot_settings,
    overseerr_api_key,
    overseerr_base_url,
    overseerr_session,
    radarr_base_url,
    radarr_session,
    requester_tag_from_user,
    sonarr_base_url,
    sonarr_session,
)

REQUESTED_RE = re.compile(r"^(?P<user>.+?)[\s_-]*requested$", re.IGNORECASE)
PAGE_SIZE = 100

APPS = {
    "radarr": {
        "base_url": radarr_base_url,
        "session": radarr_session,
        "api_key_env": "RADARR_API_KEY",
        "list_path": "/api/v3/movie",
        "editor_path": "/api/v3/movie/editor",
        "ids_field": "movieIds",
        "overseerr_type": "movie",
        "overseerr_id_field": "tmdbId",
    },
    "sonarr": {
        "base_url": sonarr_base_url,
        "session": sonarr_session,
        "api_key_env": "SONARR_API_KEY",
        "list_path": "/api/v3/series",
        "editor_path": "/api/v3/series/editor",
        "ids_field": "seriesIds",
        "overseerr_type": "tv",
        "overseerr_id_field": "tvdbId",
    },
}


def canonical_requested_label(label: str) -> str | None:
    """Return the canonical '<slug>-requested' for a requester tag, else None."""
    m = REQUESTED_RE.match(label.strip())
    if not m:
        return None
    slug = _clean_username(m.group("user"))
    return f"{slug}-requested" if slug else None


def plan_tag_merges(tags: list[dict]) -> dict[int, tuple[int | None, str]]:
    """Map non-canonical requester tag id -> (existing canonical id or None, canonical label)."""
    by_label = {t["label"]: t["id"] for t in tags}
    merges: dict[int, tuple[int | None, str]] = {}
    for t in tags:
        canonical = canonical_requested_label(t["label"])
        if canonical and canonical != t["label"]:
            merges[t["id"]] = (by_label.get(canonical), canonical)
    return merges


def build_overseerr_index(
    requests_: list[dict], id_field: str, media_type: str
) -> dict[int, list[str]]:
    """Map media id -> ordered, de-duplicated requester tag labels (oldest request first)."""
    index: dict[int, list[str]] = defaultdict(list)
    for req in sorted(requests_, key=lambda r: r.get("createdAt") or ""):
        media = req.get("media") or {}
        if str(media.get("mediaType", "")).lower() != media_type or not media.get(id_field):
            continue
        label = requester_tag_from_user(req.get("requestedBy"))
        if label not in index[media[id_field]]:
            index[media[id_field]].append(label)
    return index


def fetch_overseerr_requests(base: str, session) -> list[dict]:
    results: list[dict] = []
    skip = 0
    while True:
        r = session.get(
            f"{base.rstrip('/')}/api/v1/request",
            params={"take": PAGE_SIZE, "skip": skip, "filter": "all", "sort": "added"},
            timeout=30,
        )
        r.raise_for_status()
        page = r.json().get("results") or []
        results.extend(page)
        if len(page) < PAGE_SIZE:
            return results
        skip += PAGE_SIZE


def get_json(session, base: str, path: str):
    r = session.get(base.rstrip("/") + path, timeout=120)
    r.raise_for_status()
    return r.json()


def bulk_edit(session, base: str, cfg: dict, item_ids: list[int], tag_ids: list[int], mode: str):
    payload = {cfg["ids_field"]: item_ids, "tags": tag_ids, "applyTags": mode}
    r = session.put(base.rstrip("/") + cfg["editor_path"], json=payload, timeout=120)
    if r.status_code not in (200, 202):
        fail(f"Bulk {mode} failed: HTTP {r.status_code} - {r.text}")


def ensure_tag_id(session, base: str, label: str, apply: bool, cache: dict[str, int]) -> int:
    if label in cache:
        return cache[label]
    if not apply:
        return -1
    r = session.post(base.rstrip("/") + "/api/v3/tag", json={"label": label}, timeout=30)
    if r.status_code not in (200, 201):
        fail(f"Creating tag '{label}' failed: HTTP {r.status_code} {r.text}")
    cache[label] = int(r.json()["id"])
    return cache[label]


def normalize(session, base, cfg, apply: bool) -> None:
    tags = get_json(session, base, "/api/v3/tag")
    merges = plan_tag_merges(tags)
    if not merges:
        print("  [normalize] no variant tags found")
        return
    items = get_json(session, base, cfg["list_path"])
    label_ids = {t["label"]: t["id"] for t in tags}
    for old_id, (_, canonical) in merges.items():
        old_label = next(t["label"] for t in tags if t["id"] == old_id)
        affected = [i["id"] for i in items if old_id in i.get("tags", [])]
        print(f"  [normalize] '{old_label}' -> '{canonical}' ({len(affected)} items)")
        if not apply:
            continue
        new_id = ensure_tag_id(session, base, canonical, apply, label_ids)
        if affected:
            bulk_edit(session, base, cfg, affected, [new_id], "add")
        d = session.delete(f"{base.rstrip('/')}/api/v3/tag/{old_id}", timeout=30)
        if d.status_code not in (200, 204):
            fail(f"Deleting tag '{old_label}' failed: HTTP {d.status_code} {d.text}")


def backfill(session, base, cfg, index, source_label: str, default_label: str, apply: bool) -> None:
    tags = get_json(session, base, "/api/v3/tag")
    label_ids = {t["label"]: t["id"] for t in tags}
    requester_ids = {t["id"] for t in tags if canonical_requested_label(t["label"])}
    to_tag: dict[str, list[int]] = defaultdict(list)
    default_ids: list[int] = []
    for item in get_json(session, base, cfg["list_path"]):
        if requester_ids.intersection(item.get("tags", [])):
            continue
        labels = index.get(item.get(cfg["overseerr_id_field"]))
        if not labels:
            default_ids.append(item["id"])
            continue
        for label in labels:
            to_tag[label].append(item["id"])
    for label, ids in sorted(to_tag.items()):
        print(f"  [backfill] '{label}': {len(ids)} items")
        if apply:
            tag_ids = [
                ensure_tag_id(session, base, label, apply, label_ids),
                ensure_tag_id(session, base, source_label, apply, label_ids),
            ]
            bulk_edit(session, base, cfg, ids, tag_ids, "add")
    if default_ids:
        print(
            f"  [backfill] '{default_label}': {len(default_ids)} items "
            "(no Overseerr request: pre-Overseerr or manually added)"
        )
        if apply:
            default_id = ensure_tag_id(session, base, default_label, apply, label_ids)
            bulk_edit(session, base, cfg, default_ids, [default_id], "add")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument(
        "--default-tag",
        default=DEFAULT_REQUESTER_LABEL,
        help="tag for items with no Overseerr request (default: %(default)s)",
    )
    ap.add_argument("--app", choices=["radarr", "sonarr", "both"], default="both")
    args = ap.parse_args()

    settings = load_bot_settings()["overseerr_requester_tagging"]
    source_label = str(settings["source_tag_label"])
    o_base = overseerr_base_url()
    o_session = overseerr_session(overseerr_api_key())
    requests_ = fetch_overseerr_requests(o_base, o_session)
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}; {len(requests_)} Overseerr requests")

    for name in ("radarr", "sonarr") if args.app == "both" else (args.app,):
        cfg = APPS[name]
        key = os.getenv(cfg["api_key_env"])
        if not key:
            fail(f"Missing {cfg['api_key_env']} in environment")
        base, session = cfg["base_url"](), cfg["session"](key)
        print(f"== {name} ({base})")
        normalize(session, base, cfg, args.apply)
        index = build_overseerr_index(requests_, cfg["overseerr_id_field"], cfg["overseerr_type"])
        backfill(session, base, cfg, index, source_label, args.default_tag, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
