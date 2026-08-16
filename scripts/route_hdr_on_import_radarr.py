#!/usr/bin/env python3
"""
route_hdr_on_import_radarr.py — Route HDR-profile movies to /movies-hdr at add time.

When a movie is added to Radarr with an HDR quality profile, this script
immediately updates its root folder to /movies-hdr — before any download
starts. No post-import reshuffling needed.

The quality profile is matched by name: any profile whose name contains "hdr"
(case-insensitive) is treated as HDR. Change RADARR_HDR_PROFILE_KEYWORDS env
var if your profile uses a different naming convention.

── Trigger setup (webhook via integrations-bot) ──────────────────────────────
One single webhook in Radarr covers all scripts — the bot dispatches by event.

  Radarr → Settings → Connect → Add (+) → Webhook
    Name:    integrations-bot
    URL:     http://integrations-bot:7777/api/v1/webhooks/radarr
    Method:  POST
    Events:  On Movie Added ✓  (also enable File Import/File Upgrade for other scripts)
    Headers: X-Webhook-Token: <value of WEBHOOK_TOKEN env var>  [optional]

  This script fires for the "MovieAdded" event.
  The bot injects radarr_eventtype and radarr_movie_id from the webhook payload.

── Env vars (set in integrations-bot container) ──────────────────────────────
  RADARR_API_KEY              (required)
  RADARR_URL                  (optional; default: http://radarr:7878)
    RADARR_HDR_ROOT             (optional; default: see bot_settings.yaml)
    RADARR_HDR_PROFILE_KEYWORDS (optional; default: see bot_settings.yaml)  comma-separated
"""

import os
import sys

from integration_utils import (
    _env,
    env_keyword_set,
    env_str,
    fail,
    load_bot_settings,
    radarr_base_url,
    radarr_session,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_SETTINGS = load_bot_settings()
HDR_SETTINGS = BOT_SETTINGS["hdr_routing"]["radarr"]

HDR_ROOT = env_str("RADARR_HDR_ROOT", HDR_SETTINGS["hdr_root"])

# Case-insensitive substrings to match against quality profile names.
HDR_PROFILE_KEYWORDS = env_keyword_set(
    "RADARR_HDR_PROFILE_KEYWORDS",
    ",".join(HDR_SETTINGS["profile_keywords"]),
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def is_hdr_profile(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in HDR_PROFILE_KEYWORDS)


def get_profile_name(s, base: str, profile_id: int) -> str:
    r = s.get(f"{base}/api/v3/qualityprofile/{profile_id}", timeout=10)
    if r.status_code == 200:
        return r.json().get("name", "")
    return ""


def set_root_folder(s, base: str, movie_id: int, root: str) -> None:
    """Update a movie's root folder (no file to move yet — movie was just added)."""
    payload = {
        "movieIds": [movie_id],
        "rootFolderPath": root,
        "moveFiles": False,
    }
    r = s.put(f"{base}/api/v3/movie/editor", json=payload, timeout=30)
    if r.status_code not in (200, 202):
        fail(f"Root folder update failed: HTTP {r.status_code} — {r.text}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    event = os.getenv("radarr_eventtype", "").strip()

    if event == "Test":
        print("[INFO] Test event received — script is working correctly.")
        sys.exit(0)

    if event != "MovieAdded":
        print(f"[INFO] Ignoring event type '{event}'.")
        sys.exit(0)

    movie_id_str = os.getenv("radarr_movie_id", "").strip()
    if not movie_id_str or not movie_id_str.isdigit():
        fail("radarr_movie_id is missing or not numeric.")
    movie_id = int(movie_id_str)

    api_key = _env("RADARR_API_KEY", required=True)
    base = radarr_base_url()
    s = radarr_session(api_key)

    r = s.get(f"{base}/api/v3/movie/{movie_id}", timeout=15)
    if r.status_code == 404:
        fail(f"Movie ID {movie_id} not found in Radarr.")
    r.raise_for_status()
    movie = r.json()

    title = movie.get("title", f"ID:{movie_id}")
    profile_id = movie.get("qualityProfileId")
    current_root = (movie.get("rootFolderPath") or "").rstrip("/")

    profile_name = get_profile_name(s, base, profile_id) if profile_id else ""

    if not is_hdr_profile(profile_name):
        print(f"[INFO] '{title}' uses profile '{profile_name}' — not HDR, no action.")
        sys.exit(0)

    if current_root == HDR_ROOT:
        print(f"[INFO] '{title}' is already in {HDR_ROOT}.")
        sys.exit(0)

    print(f"[INFO] '{title}' added with profile '{profile_name}' — routing to {HDR_ROOT} …")
    set_root_folder(s, base, movie_id, HDR_ROOT)
    print(f"[DONE] '{title}' root folder set to {HDR_ROOT}.")


if __name__ == "__main__":
    main()
