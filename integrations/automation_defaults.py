"""Shared defaults and env helpers for automation integrations."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 180

RADARR_EVENT_SCRIPTS: dict[str, list[str]] = {
    "MovieAdded": ["route_hdr_on_import_radarr.py"],
    "Download": ["tag_overseerr_requester_radarr.py", "audio_fix_radarr.py"],
}

RADARR_EVENT_ALIASES: dict[str, str] = {
    "download": "Download",
    "import": "Download",
    "file_import": "Download",
    "on_file_import": "Download",
    "upgrade": "Download",
    "file_upgrade": "Download",
    "on_file_upgrade": "Download",
    "movieadded": "MovieAdded",
    "movie_added": "MovieAdded",
    "on_movie_added": "MovieAdded",
}

SONARR_EVENT_SCRIPTS: dict[str, list[str]] = {
    "SeriesAdd": ["route_hdr_on_import_sonarr.py"],
    "Download": ["tag_overseerr_requester_sonarr.py", "audio_fix_sonarr.py"],
}

SONARR_EVENT_ALIASES: dict[str, str] = {
    "download": "Download",
    "import": "Download",
    "on_import": "Download",
    "upgrade": "Download",
    "on_upgrade": "Download",
    "seriesadd": "SeriesAdd",
    "series_add": "SeriesAdd",
    "on_series_add": "SeriesAdd",
}

TAUTULLI_EVENT_SCRIPTS: dict[str, str] = {
    "watched": "tag_radarr_watched_tautulli.py",
}


def resolve_timeout_seconds(env_var: str, default: int = DEFAULT_SCRIPT_TIMEOUT_SECONDS) -> int:
    """Resolve timeout as int from env var with safe default on parse errors."""
    raw = os.getenv(env_var, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def extract_event(payload: dict, *keys: str) -> str:
    """Extract first non-empty event value from payload keys."""
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value).strip()
    return ""


def normalize_event_token(value: str) -> str:
    """Normalize free-form event values for map lookups."""
    return (value or "").strip().lower().replace(" ", "_")


def canonical_radarr_event_type(value: str) -> str:
    """Map Radarr UI labels and aliases to the webhook eventType value."""
    event_type = (value or "").strip()
    if not event_type:
        return ""
    return RADARR_EVENT_ALIASES.get(normalize_event_token(event_type), event_type)


def canonical_sonarr_event_type(value: str) -> str:
    """Map Sonarr UI labels and aliases to the webhook eventType value."""
    event_type = (value or "").strip()
    if not event_type:
        return ""
    return SONARR_EVENT_ALIASES.get(normalize_event_token(event_type), event_type)


def radarr_scripts_for_event(event_type: str) -> list[str]:
    """Return Radarr scripts for a given Radarr event type."""
    return RADARR_EVENT_SCRIPTS.get(canonical_radarr_event_type(event_type), [])


def sonarr_scripts_for_event(event_type: str) -> list[str]:
    """Return Sonarr scripts for a given Sonarr event type."""
    return SONARR_EVENT_SCRIPTS.get(canonical_sonarr_event_type(event_type), [])


def tautulli_script_for_event(event: str) -> str | None:
    """Return Tautulli script name for a normalized event token."""
    return TAUTULLI_EVENT_SCRIPTS.get(event)
