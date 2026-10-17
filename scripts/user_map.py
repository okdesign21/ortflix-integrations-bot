#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
user_map.py — Map Plex usernames to the tag name used for that person.

Watched tags use the Plex username while requested tags use the Overseerr one; this map
makes both use the same name. Stored in user_map.json:

  {"users": {"chen": {"tag": "chencoh2", "manual": true, "nickname": "Chen C."},
             "legal94": {"tag": "legalizem4", "manual": false}}}

"nickname" is an optional free-text label for humans; nothing uses it yet.

Entries with "manual": true are locked: automatic sync never changes them.

Lookup order for a user: the map first; if absent, the user is matched to Overseerr
(by Plex username or email via Tautulli) and added to the map as an automatic entry.

File location: $ORTFLIX_USER_MAP_DIR, else /config if it exists, else the repo config/.
Manage it with scripts/map_users.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from integration_utils import (
    _clean_username,
    overseerr_api_key,
    overseerr_base_url,
    overseerr_session,
    requester_tag_from_user,
)

MAP_FILE = "user_map.json"
REPO_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PAGE_SIZE = 100
UNKNOWN = "unknown"


def map_dir() -> Path:
    raw = os.getenv("ORTFLIX_USER_MAP_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    mounted = Path("/config")
    return mounted if mounted.is_dir() else REPO_CONFIG_DIR


def map_path() -> Path:
    return map_dir() / MAP_FILE


def user_key(name: str | None) -> str | None:
    """Normalized map key for a Plex username (None if it has no ASCII-safe characters)."""
    return _clean_username(name)


def load_user_map(path: Path | None = None) -> dict[str, dict]:
    path = path or map_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read user map {path}: {exc}") from exc
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, dict):
        raise ValueError(f"Invalid user map {path}: expected an object with a 'users' object")
    entries: dict[str, dict] = {}
    for name, entry in users.items():
        key, tag = user_key(name), user_key((entry or {}).get("tag"))
        if key and tag:
            entries[key] = {"tag": tag, "manual": bool(entry.get("manual", False))}
            if entry.get("nickname"):
                entries[key]["nickname"] = str(entry["nickname"]).strip()
    return entries


def save_user_map(entries: dict[str, dict], path: Path | None = None) -> Path:
    """Atomically write the map so concurrent script runs never see a partial file."""
    path = path or map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"users": dict(sorted(entries.items()))}, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def tautulli_credentials() -> tuple[str, str] | None:
    base, key = os.getenv("TAUTULLI_URL"), os.getenv("TAUTULLI_API_KEY")
    return (base, key) if base and key else None


def fetch_tautulli_users() -> list[dict]:
    creds = tautulli_credentials()
    if not creds:
        raise ValueError("TAUTULLI_URL/TAUTULLI_API_KEY not set")
    r = requests.get(
        f"{creds[0].rstrip('/')}/api/v2",
        params={"apikey": creds[1], "cmd": "get_users"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["response"]["data"]


def fetch_overseerr_users() -> list[dict]:
    base, session = overseerr_base_url(), overseerr_session(overseerr_api_key())
    users: list[dict] = []
    while True:
        r = session.get(
            f"{base.rstrip('/')}/api/v1/user",
            params={"take": PAGE_SIZE, "skip": len(users)},
            timeout=30,
        )
        r.raise_for_status()
        page = r.json().get("results") or []
        users.extend(page)
        if len(page) < PAGE_SIZE:
            return users


def propose_tag(plex_username: str, email: str | None, overseerr_users: list[dict]) -> str:
    """Tag name for a Plex user: their Overseerr tag if matched, else their own slug."""
    own = user_key(plex_username) or UNKNOWN
    name, mail = plex_username.lower(), (email or "").lower()
    for user in overseerr_users:
        plex_match = user.get("plexUsername") and user["plexUsername"].lower() == name
        email_match = mail and (user.get("email") or "").lower() == mail
        if plex_match or email_match:
            tag = requester_tag_from_user(user).removesuffix("-requested")
            return own if tag == UNKNOWN else tag
    return own


def resolve_user(username: str | None, persist: bool = True) -> str:
    """Tag name for a Plex username: map first; otherwise match via Overseerr and add it."""
    key = user_key(username)
    if not key:
        return UNKNOWN
    entries = load_user_map()
    if key in entries:
        return entries[key]["tag"]
    try:
        email = next(
            (u.get("email") for u in fetch_tautulli_users() if user_key(u.get("username")) == key),
            "",
        )
        tag = propose_tag(username, email, fetch_overseerr_users())
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"[user_map] Lookup failed for {key!r}, not saved: {exc}", file=sys.stderr)
        return key
    if persist:
        entries = load_user_map()
        entries.setdefault(key, {"tag": tag, "manual": False})
        save_user_map(entries)
        print(f"[user_map] Added {key!r} -> {tag!r}")
    return entries.get(key, {"tag": tag})["tag"]
