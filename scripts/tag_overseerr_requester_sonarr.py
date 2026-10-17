#!/usr/bin/env python3
"""
tag_overseerr_requester_sonarr.py — Tag a Sonarr series with who requested it in Overseerr.

On import or upgrade, looks up the matching Overseerr request and tags the
series in Sonarr with "{requester}-requested" and "overseer". With no matching request
it only adds "okdesign-requested" (the "overseer" tag is for Overseerr requests only).

── Trigger setup (webhook via integrations-bot) ──────────────────────────────
One single webhook in Sonarr covers all scripts — the bot dispatches by event.

  Sonarr → Settings → Connect → Add (+) → Webhook
    Name:    integrations-bot
    URL:     http://integrations-bot:7777/api/v1/webhooks/sonarr
    Method:  POST
    Events:  On Import ✓   On Upgrade ✓   On Series Add ✓
    Headers: X-Webhook-Token: <value of WEBHOOK_TOKEN env var>  [optional]

  This script fires for the "Download" payload event.
  Sonarr uses that payload value for both On Import and On Upgrade.
  The bot injects sonarr_eventtype, sonarr_series_tvdbid, sonarr_series_imdbid,
  sonarr_series_title, sonarr_series_year from the webhook payload.

── Env vars (set in integrations-bot container) ──────────────────────────────
  SONARR_API_KEY     (required)
  SONARR_URL         (optional; default: http://sonarr:8989)
  OVERSEERR_API_KEY  or SEERR_API_KEY  (required)
  OVERSEERR_URL      or SEERR_URL      (optional; default: http://seerr:5055)
"""

import os
import sys

from integration_utils import (
    DEFAULT_REQUESTER_LABEL,
    _env,
    add_tags_to_series,
    ensure_tag,
    fail,
    find_overseerr_request,
    find_series,
    load_bot_settings,
    overseerr_api_key,
    overseerr_base_url,
    overseerr_session,
    requester_tag_from_user,
    sonarr_base_url,
    sonarr_session,
)

BOT_SETTINGS = load_bot_settings()
REQUESTER_TAGGING = BOT_SETTINGS["overseerr_requester_tagging"]
REQUEST_QUERY = dict(REQUESTER_TAGGING["request_query"])
SOURCE_TAG_LABEL = str(REQUESTER_TAGGING["source_tag_label"])


def main() -> None:
    event = os.environ.get("sonarr_eventtype", "")
    if event == "Test":
        print("[tag_overseerr_requester_sonarr] Test event received — script is working.")
        sys.exit(0)

    if event not in ("Download", "Upgrade"):
        print(f"[tag_overseerr_requester_sonarr] Skipping event type: {event!r}")
        sys.exit(0)

    tvdb_id = os.environ.get("sonarr_series_tvdbid")
    imdb_id = os.environ.get("sonarr_series_imdbid")
    title = os.environ.get("sonarr_series_title")
    year = os.environ.get("sonarr_series_year")

    sonarr_url = sonarr_base_url()
    sonarr_api_key = _env("SONARR_API_KEY", required=True)
    overseerr_url = overseerr_base_url()
    overseerr_key = overseerr_api_key()

    s_session = sonarr_session(sonarr_api_key)
    o_session = overseerr_session(overseerr_key)

    try:
        series = find_series(
            s_session,
            sonarr_url,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            title=title,
            year=year,
        )
    except Exception as e:
        fail(f"Sonarr lookup failed: {e}")

    if not series:
        fail(
            f"No matching series in Sonarr "
            f"(tvdb={tvdb_id!r}, imdb={imdb_id!r}, title={title!r}, year={year!r})"
        )

    series_id = series.get("id")
    if not series_id:
        fail("Matched Sonarr series missing 'id'.")

    try:
        req = find_overseerr_request(
            o_session,
            overseerr_url,
            REQUEST_QUERY,
            media_types={"tv", "series", ""},
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            title=title,
            year=year,
        )
    except Exception as e:
        fail(f"Overseerr lookup failed: {e}")

    user = req.get("requestedBy") if req else None
    tag_label = requester_tag_from_user(user) if req else DEFAULT_REQUESTER_LABEL

    try:
        tag_ids = [ensure_tag(s_session, sonarr_url, tag_label)]
        if req:
            tag_ids.append(ensure_tag(s_session, sonarr_url, SOURCE_TAG_LABEL))
        add_tags_to_series(s_session, sonarr_url, int(series_id), tag_ids)
    except Exception as e:
        fail(f"Applying tags failed: {e}")

    requester_display = (
        next(
            (user[k] for k in ("displayName", "username", "email") if user and user.get(k)),
            "unknown",
        )
        if user
        else "unknown"
    )
    applied = [tag_label, SOURCE_TAG_LABEL] if req else [tag_label]
    print(
        f"[Sonarr] Tagged '{series.get('title')}' ({series.get('year')}) "
        f"with {applied} "
        f"(id={series_id}, requester={requester_display})"
    )


if __name__ == "__main__":
    main()
