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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import requests
from integration_utils import (
    _clean_username,
    overseerr_base_url,
    overseerr_session,
    read_overseerr_api_key,
    requester_tag_from_user,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms have no inter-process lock
    fcntl = None

MAP_FILE = "user_map.json"
REPO_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PAGE_SIZE = 100
UNKNOWN = "unknown"


class TautulliError(ValueError):
    """Tautulli request failed; the message never contains the request URL or API key."""


class TautulliResultError(TautulliError):
    """Tautulli answered, but with result != success (e.g. unknown rating key)."""


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


@contextmanager
def map_lock() -> Iterator[None]:
    """Exclusive inter-process lock so load-modify-save of the map cannot interleave."""
    if fcntl is None:
        yield
        return
    lock_path = map_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def tautulli_credentials() -> tuple[str, str] | None:
    base, key = os.getenv("TAUTULLI_URL"), os.getenv("TAUTULLI_API_KEY")
    return (base, key) if base and key else None


def tautulli_call(base: str, key: str, cmd: str, timeout: int = 30, **params):
    """Call the Tautulli API and return its data; errors are raised without the API key."""
    try:
        r = requests.get(
            f"{base.rstrip('/')}/api/v2",
            params={"apikey": key, "cmd": cmd, **params},
            timeout=timeout,
        )
        r.raise_for_status()
        body = r.json()["response"]
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "error"
        raise TautulliError(f"Tautulli {cmd} failed: HTTP {status}") from None
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise TautulliError(f"Tautulli {cmd} failed: {type(exc).__name__}") from None
    if body.get("result") != "success":
        raise TautulliResultError(f"Tautulli {cmd} failed: {body.get('message')}")
    return body["data"]


def fetch_tautulli_users() -> list[dict]:
    creds = tautulli_credentials()
    if not creds:
        raise ValueError("TAUTULLI_URL/TAUTULLI_API_KEY not set")
    return tautulli_call(creds[0], creds[1], "get_users")


def fetch_overseerr_users() -> list[dict]:
    api_key = read_overseerr_api_key()
    if not api_key:
        raise ValueError("OVERSEERR_API_KEY/SEERR_API_KEY not set")
    base, session = overseerr_base_url(), overseerr_session(api_key)
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
    try:
        entries = load_user_map()
    except ValueError as exc:
        print(f"[user_map] {exc}; using {key!r}", file=sys.stderr)
        return key
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
    if not persist:
        return tag
    try:
        with map_lock():
            entries = load_user_map()
            entries.setdefault(key, {"tag": tag, "manual": False})
            save_user_map(entries)
    except (OSError, ValueError) as exc:
        print(f"[user_map] Could not save {key!r}: {exc}", file=sys.stderr)
        return tag
    print(f"[user_map] Added {key!r} -> {entries[key]['tag']!r}")
    return entries[key]["tag"]
