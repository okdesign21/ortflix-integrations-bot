#!/usr/bin/env python3
"""
route_hdr_on_import_sonarr.py — Route HDR-profile series to an HDR root folder at add time.

When a series is added to Sonarr with an HDR quality profile, this script
immediately updates its root folder to /tv-hdr — before any download starts.

── Trigger setup (webhook via integrations-bot) ──────────────────────────────
One single webhook in Sonarr covers all scripts — the bot dispatches by event.

  Sonarr → Settings → Connect → Add (+) → Webhook
    Name:    integrations-bot
    URL:     http://integrations-bot:7777/api/v1/webhooks/sonarr
    Method:  POST
    Events:  On Series Add ✓  (also enable Download/Upgrade for other scripts)
    Headers: X-Webhook-Token: <value of WEBHOOK_TOKEN env var>  [optional]

  This script fires for the "SeriesAdd" payload event.
  The bot injects sonarr_eventtype and sonarr_series_id from the webhook payload.

── Env vars (set in integrations-bot container) ──────────────────────────────
  SONARR_API_KEY              (required)
  SONARR_URL                  (optional; default: http://sonarr:8989)
    SONARR_HDR_ROOT             (optional; default: see bot_settings.yaml)
    SONARR_HDR_PROFILE_KEYWORDS (optional; default: see bot_settings.yaml)  comma-separated
"""

import os
import sys

from integration_utils import (
    _env,
    env_keyword_set,
    env_str,
    fail,
    load_bot_settings,
    sonarr_base_url,
    sonarr_session,
)

BOT_SETTINGS = load_bot_settings()
HDR_SETTINGS = BOT_SETTINGS["hdr_routing"]["sonarr"]

HDR_ROOT = env_str("SONARR_HDR_ROOT", HDR_SETTINGS["hdr_root"])
HDR_PROFILE_KEYWORDS = env_keyword_set(
    "SONARR_HDR_PROFILE_KEYWORDS",
    ",".join(HDR_SETTINGS["profile_keywords"]),
)


def is_hdr_profile(name: str) -> bool:
    n = (name or "").lower()
    return any(kw in n for kw in HDR_PROFILE_KEYWORDS)


def get_profile_name(s, base: str, profile_id: int) -> str:
    r = s.get(f"{base}/api/v3/qualityprofile/{profile_id}", timeout=10)
    if r.status_code == 200:
        return r.json().get("name", "")
    return ""


def set_root_folder(s, base: str, series_id: int, root: str) -> None:
    payload = {
        "seriesIds": [series_id],
        "rootFolderPath": root,
        "moveFiles": False,
    }
    r = s.put(f"{base}/api/v3/series/editor", json=payload, timeout=30)
    if r.status_code not in (200, 202):
        fail(f"Root folder update failed: HTTP {r.status_code} — {r.text}")


def main() -> None:
    event = os.getenv("sonarr_eventtype", "").strip()

    if event == "Test":
        print("[INFO] Test event received — script is working correctly.")
        sys.exit(0)

    if event != "SeriesAdd":
        print(f"[INFO] Ignoring event type '{event}'.")
        sys.exit(0)

    series_id_str = os.getenv("sonarr_series_id", "").strip()
    if not series_id_str.isdigit():
        fail("sonarr_series_id is missing or not numeric.")
    series_id = int(series_id_str)

    api_key = _env("SONARR_API_KEY", required=True)
    base = sonarr_base_url()
    s = sonarr_session(api_key)

    r = s.get(f"{base}/api/v3/series/{series_id}", timeout=15)
    if r.status_code == 404:
        fail(f"Series ID {series_id} not found in Sonarr.")
    r.raise_for_status()
    series = r.json()

    title = series.get("title", f"ID:{series_id}")
    profile_id = series.get("qualityProfileId")
    current_root = (series.get("rootFolderPath") or "").rstrip("/")
    profile_name = get_profile_name(s, base, profile_id) if profile_id else ""

    if not is_hdr_profile(profile_name):
        print(f"[INFO] '{title}' uses profile '{profile_name}' — not HDR, no action.")
        sys.exit(0)

    if current_root == HDR_ROOT:
        print(f"[INFO] '{title}' is already in {HDR_ROOT}.")
        sys.exit(0)

    print(f"[INFO] '{title}' added with profile '{profile_name}' — routing to {HDR_ROOT} …")
    set_root_folder(s, base, series_id, HDR_ROOT)
    print(f"[DONE] '{title}' root folder set to {HDR_ROOT}.")


if __name__ == "__main__":
    main()
