#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Shared utilities for integrations scripts.
# Provides common helpers for Radarr, Sonarr, Overseerr, Telegram, and Discord integrations.

from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

import requests
import yaml


# ====== Common Helpers ======
def _env(name, required=False, default=None):
    """Get environment variable, exit if required but missing."""
    v = os.getenv(name, default)
    if required and not v:
        print(f"[ERROR] Missing env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def env_str(name: str, default: str) -> str:
    """Get string env var with a default value."""
    return str(os.getenv(name, default))


def env_int(name: str, default: int) -> int:
    """Get integer env var with safe parse default."""
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def env_csv_list(name: str, default_csv: str) -> list[str]:
    """Read comma-separated env var into a cleaned list."""
    raw = os.getenv(name, default_csv)
    return [part.strip() for part in raw.split(",") if part.strip()]


def env_keyword_set(name: str, default_csv: str) -> set[str]:
    """Read comma-separated env var into a lowercased keyword set."""
    return {part.lower() for part in env_csv_list(name, default_csv)}


def resolve_secret_reference(raw: str | None) -> str | None:
    """Resolve a literal secret, file path, or `sudo cat /path` reference."""
    if raw is None:
        return None
    secret = raw.strip()
    if not secret:
        return None

    command_match = re.match(r"^(?:sudo\s+)?cat\s+(.+)$", secret, re.IGNORECASE)
    if command_match:
        path = Path(command_match.group(1).strip().strip('"').strip("'")).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        try:
            result = subprocess.run(
                ["sudo", "cat", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, TimeoutError):
            return None
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    path = Path(secret).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return secret


def resolve_plex_url(default: str) -> str:
    """Resolve Plex base URL from env override or config default."""
    url = os.getenv("PLEX_URL", "").strip()
    if url:
        return url.rstrip("/")
    return default.rstrip("/")


def resolve_plex_token(default_token_file: str) -> str:
    """Resolve Plex token from env override or configured token file."""
    token_file = (os.getenv("PLEX_TOKEN_FILE") or default_token_file or "").strip()
    if token_file:
        path = Path(token_file).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()

    token = resolve_secret_reference(os.getenv("PLEX_TOKEN"))
    if token:
        return token
    return ""


DEFAULT_BOT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "bot_settings.yaml"
LOCAL_BOT_SETTINGS_PATH = DEFAULT_BOT_SETTINGS_PATH.with_name("bot_settings.local.yaml")
BOT_SETTINGS_ENV_VAR = "ORTFLIX_BOT_SETTINGS_FILE"
BOT_SETTINGS_OVERRIDE_CANDIDATES = (
    LOCAL_BOT_SETTINGS_PATH,
    Path("/config/bot_settings.yaml"),
    Path("/config/bot_settings.yml"),
)


def _load_settings_file(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        fail(f"Missing config file: {path}")
    except yaml.YAMLError as exc:
        fail(f"Invalid YAML in {path}: {exc}")

    if not isinstance(data, dict):
        fail(f"Invalid config shape in {path}: expected top-level object")
    return data


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def resolve_bot_settings_override_path() -> Path | None:
    raw_path = os.getenv(BOT_SETTINGS_ENV_VAR, "").strip()
    if raw_path:
        path = Path(raw_path).expanduser()
        if not path.is_file():
            fail(f"{BOT_SETTINGS_ENV_VAR} points to a missing file: {path}")
        return path

    for candidate in BOT_SETTINGS_OVERRIDE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_bot_settings() -> dict:
    """Load bundled bot settings plus an optional mounted override file."""
    settings = _load_settings_file(DEFAULT_BOT_SETTINGS_PATH)
    override_path = resolve_bot_settings_override_path()
    if override_path and override_path != DEFAULT_BOT_SETTINGS_PATH:
        settings = _deep_merge_dicts(settings, _load_settings_file(override_path))
    return settings


def fail(msg, code=1):
    """Print error and exit."""
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def _clean_username(name: Optional[str]) -> Optional[str]:
    """Slugify username to ASCII alphanumeric and hyphen characters.

    Radarr and Sonarr require ASCII-safe tag labels.
    Returns None if username contains non-ASCII characters or no alphanumeric characters.
    """
    if not name:
        return None
    # Only keep ASCII letters, digits, and replace others with hyphens
    cleaned = "".join(ch.lower() if (ch.isascii() and ch.isalnum()) else "-" for ch in str(name))
    cleaned = cleaned.strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    # If no valid ASCII alphanumeric chars remain, return None.
    if not cleaned or not any(ch.isascii() and ch.isalnum() for ch in cleaned):
        return None
    return cleaned


# Tag for items with no Overseerr request (pre-Overseerr or manually added).
DEFAULT_REQUESTER_LABEL = "okdesign-requested"


def requester_tag_from_user(user: Optional[dict]) -> str:
    """Build a stable requester tag with username/displayName/email defaults."""
    if not user:
        return "unknown-requested"
    for key in ("displayName", "username"):
        val = user.get(key)
        if val:
            cleaned = _clean_username(val)
            if cleaned:
                return f"{cleaned}-requested"
    email = user.get("email")
    if email and "@" in email:
        cleaned = _clean_username(email.split("@")[0])
        if cleaned:
            return f"{cleaned}-requested"
    return "unknown-requested"


# ====== Base URL Helpers ======
def radarr_base_url() -> str:
    """Get Radarr base URL from env override or shared config default."""
    default_url = (
        load_bot_settings().get("shared", {}).get("services", {}).get("radarr", {}).get("url")
        or "http://radarr:7878"
    )
    url = os.getenv("RADARR_URL", "").strip()
    if url:
        return url.rstrip("/")
    host = os.getenv("RADARR_HOST", "").strip()
    port = os.getenv("RADARR_PORT", "").strip()
    if host or port:
        return f"http://{host or 'radarr'}:{port or '7878'}".rstrip("/")
    return str(default_url).rstrip("/")


def sonarr_base_url() -> str:
    """Get Sonarr base URL from env override or shared config default."""
    default_url = (
        load_bot_settings().get("shared", {}).get("services", {}).get("sonarr", {}).get("url")
        or "http://sonarr:8989"
    )
    url = os.getenv("SONARR_URL", "").strip()
    if url:
        return url.rstrip("/")
    host = os.getenv("SONARR_HOST", "").strip()
    port = os.getenv("SONARR_PORT", "").strip()
    if host or port:
        return f"http://{host or 'sonarr'}:{port or '8989'}".rstrip("/")
    return str(default_url).rstrip("/")


def overseerr_base_url() -> str:
    """Get Seerr/Overseerr base URL from env.

    Resolution order:
    1) OVERSEERR_URL
    2) SEERR_URL
    3) http://<OVERSEERR_HOST|SEERR_HOST|overseerr>:<OVERSEERR_PORT|SEERR_PORT|5055>
    4) shared config default
    """
    url = (os.getenv("OVERSEERR_URL") or os.getenv("SEERR_URL") or "").strip()
    if url:
        return url.rstrip("/")

    host = os.getenv("OVERSEERR_HOST") or os.getenv("SEERR_HOST")
    port = os.getenv("OVERSEERR_PORT") or os.getenv("SEERR_PORT")
    if host or port:
        return f"http://{host or 'overseerr'}:{port or '5055'}".rstrip("/")

    default_url = (
        load_bot_settings().get("shared", {}).get("services", {}).get("overseerr", {}).get("url")
        or "http://overseerr:5055"
    )
    return str(default_url).rstrip("/")


# ====== Session Factories ======
def radarr_session(api_key: str) -> requests.Session:
    """Create Radarr API session with key."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "X-Api-Key": api_key})
    return s


def sonarr_session(api_key: str) -> requests.Session:
    """Create Sonarr API session with key."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "X-Api-Key": api_key})
    return s


def overseerr_api_key() -> str:
    """Get Overseerr API key from env.

    Resolution order:
    1) OVERSEERR_API_KEY
    2) SEERR_API_KEY
    """
    key = os.getenv("OVERSEERR_API_KEY") or os.getenv("SEERR_API_KEY")
    if not key:
        fail("Missing OVERSEERR_API_KEY or SEERR_API_KEY in environment")
    return key


def overseerr_session(api_key: str) -> requests.Session:
    """Create Overseerr API session with key."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "X-Api-Key": api_key})
    return s


def _media_matches(
    media: dict,
    *,
    tmdb_id=None,
    tvdb_id=None,
    imdb_id=None,
    title=None,
    year=None,
) -> bool:
    if tmdb_id and str(media.get("tmdbId")) == str(tmdb_id):
        return True
    if tvdb_id and str(media.get("tvdbId")) == str(tvdb_id):
        return True
    if imdb_id and str(media.get("imdbId", "")).lower() == str(imdb_id).lower():
        return True
    if title and str(media.get("title", "")).lower() == str(title).lower():
        if year is None or str(media.get("year")) == str(year):
            return True
    return False


def find_overseerr_request(
    s: requests.Session,
    base: str,
    request_query: dict,
    *,
    media_types: set[str] | None = None,
    tmdb_id=None,
    tvdb_id=None,
    imdb_id=None,
    title=None,
    year=None,
) -> dict | None:
    """Find matching Overseerr request by media identifiers and title/year."""
    r = s.get(f"{base.rstrip('/')}/api/v1/request", params=request_query, timeout=15)
    r.raise_for_status()

    data = r.json()
    results = (data.get("results") if isinstance(data, dict) else data) or []
    for req in results:
        media = req.get("media") or {}
        if media_types is not None:
            media_type = str(media.get("mediaType", "")).lower()
            if media_type not in media_types:
                continue
        if _media_matches(
            media,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            title=title,
            year=year,
        ):
            return req
    return None


# ====== Radarr/Sonarr Common ======
def ensure_tag(s: requests.Session, base: str, label: str) -> int:
    """Return tag id by label (case-insensitive); create if missing."""
    r = s.get(f"{base.rstrip('/')}/api/v3/tag", timeout=15)
    r.raise_for_status()
    for t in r.json():
        if str(t.get("label", "")).strip().lower() == label.strip().lower():
            return int(t["id"])
    cr = s.post(f"{base.rstrip('/')}/api/v3/tag", json={"label": label}, timeout=15)
    if cr.status_code not in (200, 201):
        fail(f"Creating tag '{label}' failed: HTTP {cr.status_code} {cr.text}")
    return int(cr.json()["id"])


# ====== Radarr ======
def find_movie(  # noqa: C901
    s: requests.Session,
    base: str,
    tmdb_id=None,
    imdb_id=None,
    title=None,
    year=None,
) -> dict | None:
    """Find movie in Radarr by tmdbId → imdbId → exact title (+year)."""
    base_url = base.rstrip("/")

    def _first_or_none(data):
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data
        return None

    # Fast path: server-side filter by tmdbId / imdbId (if supported)
    if tmdb_id and str(tmdb_id).isdigit():
        try:
            r = s.get(
                f"{base_url}/api/v3/movie",
                params={"tmdbId": str(tmdb_id)},
                timeout=10,
            )
            if r.status_code == 200:
                data = _first_or_none(r.json())
                if data and str(data.get("tmdbId")) == str(tmdb_id):
                    return data
        except Exception:  # nosec - intentionally suppress API errors during fast-path lookup
            pass

    if imdb_id:
        try:
            r = s.get(
                f"{base_url}/api/v3/movie",
                params={"imdbId": str(imdb_id)},
                timeout=10,
            )
            if r.status_code == 200:
                data = _first_or_none(r.json())
                if (
                    data
                    and str(data.get("imdbId", "")).strip().lower() == str(imdb_id).strip().lower()
                ):
                    return data
        except Exception:  # nosec - intentionally suppress API errors during fast-path lookup
            pass

    r = s.get(f"{base_url}/api/v3/movie", timeout=20)
    r.raise_for_status()
    movies = r.json()

    # TMDb first
    if tmdb_id and str(tmdb_id).isdigit():
        for m in movies:
            if str(m.get("tmdbId")) == str(tmdb_id):
                return m

    # IMDb match
    if imdb_id:
        iid = str(imdb_id).strip().lower()
        for m in movies:
            if str(m.get("imdbId", "")).strip().lower() == iid:
                return m

    # Title + year match
    if title:
        tl = str(title).strip().lower()
        candidates = [m for m in movies if str(m.get("title", "")).strip().lower() == tl]
        if year:
            for m in candidates:
                if str(m.get("year")) == str(year):
                    return m
        if len(candidates) == 1:
            return candidates[0]

    return None


def add_tags_to_movie(s: requests.Session, base: str, movie_id: int, tag_ids: list[int]) -> None:
    """Add tags to a movie using bulk editor."""
    payload = {"movieIds": [movie_id], "tags": list(set(tag_ids)), "applyTags": "add"}
    r = s.put(f"{base.rstrip('/')}/api/v3/movie/editor", json=payload, timeout=25)
    if r.status_code not in (200, 202):
        fail(f"Tagging failed: HTTP {r.status_code} - {r.text}")


# ====== Sonarr ======
def find_series(  # noqa: C901
    s: requests.Session,
    base: str,
    tvdb_id=None,
    imdb_id=None,
    title=None,
    year=None,
) -> dict | None:
    """Find series in Sonarr by tvdbId → imdbId → exact title (+year)."""
    r = s.get(f"{base.rstrip('/')}/api/v3/series", timeout=40)
    r.raise_for_status()
    series = r.json()

    if tvdb_id and str(tvdb_id).isdigit():
        for srs in series:
            if str(srs.get("tvdbId")) == str(tvdb_id):
                return srs

    if imdb_id:
        iid = str(imdb_id).strip().lower()
        for srs in series:
            if str(srs.get("imdbId", "")).strip().lower() == iid:
                return srs

    if title:
        tl = str(title).strip().lower()
        candidates = [srs for srs in series if str(srs.get("title", "")).strip().lower() == tl]
        if year:
            for srs in candidates:
                if str(srs.get("year")) == str(year):
                    return srs
        if len(candidates) == 1:
            return candidates[0]

    return None


def add_tags_to_series(s: requests.Session, base: str, series_id: int, tag_ids: list[int]) -> None:
    """Add tags to a series using bulk editor."""
    payload = {"seriesIds": [series_id], "tags": list(set(tag_ids)), "applyTags": "add"}
    r = s.put(f"{base.rstrip('/')}/api/v3/series/editor", json=payload, timeout=25)
    if r.status_code not in (200, 202):
        fail(f"Tagging failed: HTTP {r.status_code} - {r.text}")


# ====== Discord ======
def discord_post(content: str, webhook_url: str = None) -> None:
    """Post a message to Discord via webhook (optional)."""
    webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=10)
        if r.status_code != 204:
            print(
                f"[WARN] Discord send failed: {r.status_code} - {r.text}",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[WARN] Discord exception: {e}", file=sys.stderr)


# ====== Telegram ======
def telegram_post(text: str) -> None:
    """Post a message to Telegram if bot token and chat id are available."""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not r.ok:
            print(f"[WARN] Telegram send failed: {r.status_code} - {r.text}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Telegram exception: {e}", file=sys.stderr)
