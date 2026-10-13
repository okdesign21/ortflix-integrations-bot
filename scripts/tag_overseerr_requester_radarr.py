#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tag_overseerr_requester_radarr.py — Tag a Radarr movie with who requested it in Overseerr.

On import or upgrade, looks up the matching Overseerr request and tags the
movie in Radarr with "{requester}-requested" and "overseer".

── Trigger setup (webhook via integrations-bot) ──────────────────────────────
One single webhook in Radarr covers all scripts — the bot dispatches by event.

  Radarr → Settings → Connect → Add (+) → Webhook
    Name:    integrations-bot
    URL:     http://integrations-bot:7777/api/v1/webhooks/radarr
    Method:  POST
    Events:  On File Import ✓   On File Upgrade ✓   On Movie Added ✓
    Headers: X-Webhook-Token: <value of WEBHOOK_TOKEN env var>  [optional]

  This script fires for the "Download" payload event.
  Radarr uses that payload value for both On File Import and On File Upgrade.
  The bot injects radarr_eventtype, radarr_movie_tmdbid, radarr_movie_imdbid,
  radarr_movie_title, radarr_movie_year from the webhook payload.

── Env vars (set in integrations-bot container) ──────────────────────────────
  RADARR_API_KEY     (required)
  RADARR_URL         (optional; default: http://radarr:7878)
  OVERSEERR_API_KEY  or SEERR_API_KEY  (required)
  OVERSEERR_URL      or SEERR_URL      (optional; default: http://seerr:5055)
"""

import os
import sys

from integration_utils import (
    _env,
    add_tags_to_movie,
    ensure_tag,
    fail,
    find_movie,
    find_overseerr_request,
    load_bot_settings,
    overseerr_api_key,
    overseerr_base_url,
    overseerr_session,
    radarr_base_url,
    radarr_session,
    requester_tag_from_user,
)

BOT_SETTINGS = load_bot_settings()
REQUESTER_TAGGING = BOT_SETTINGS["overseerr_requester_tagging"]
REQUEST_QUERY = dict(REQUESTER_TAGGING["request_query"])
SOURCE_TAG_LABEL = str(REQUESTER_TAGGING["source_tag_label"])


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    event = os.environ.get("radarr_eventtype", "")

    if event == "Test":
        print("[tag_overseerr_requester_radarr] Test event received — script is working.")
        sys.exit(0)

    if event != "Download":
        print(f"[tag_overseerr_requester_radarr] Skipping event type: {event!r}")
        sys.exit(0)

    tmdb_id = os.environ.get("radarr_movie_tmdbid")
    imdb_id = os.environ.get("radarr_movie_imdbid")
    title = os.environ.get("radarr_movie_title")
    year = os.environ.get("radarr_movie_year")

    radarr_url = radarr_base_url()
    radarr_api_key = _env("RADARR_API_KEY", required=True)
    overseerr_url = overseerr_base_url()
    overseerr_key = overseerr_api_key()

    r_session = radarr_session(radarr_api_key)
    o_session = overseerr_session(overseerr_key)

    try:
        movie = find_movie(
            r_session, radarr_url, tmdb_id=tmdb_id, imdb_id=imdb_id, title=title, year=year
        )
    except Exception as e:
        fail(f"Radarr lookup failed: {e}")

    if not movie:
        fail(
            f"No matching movie in Radarr "
            f"(tmdb={tmdb_id!r}, imdb={imdb_id!r}, title={title!r}, year={year!r})"
        )

    movie_id = movie.get("id")
    if not movie_id:
        fail("Matched Radarr movie missing 'id'.")

    try:
        req = find_overseerr_request(
            o_session,
            overseerr_url,
            REQUEST_QUERY,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            title=title,
            year=year,
        )
    except Exception as e:
        fail(f"Overseerr lookup failed: {e}")

    user = req.get("requestedBy") if req else None
    tag_label = requester_tag_from_user(user)

    try:
        tag_id = ensure_tag(r_session, radarr_url, tag_label)
        overseer_id = ensure_tag(r_session, radarr_url, SOURCE_TAG_LABEL)
        add_tags_to_movie(r_session, radarr_url, int(movie_id), [tag_id, overseer_id])
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

    print(
        f"[Radarr] Tagged '{movie.get('title')}' ({movie.get('year')}) "
        f"with ['{tag_label}', '{SOURCE_TAG_LABEL}'] "
        f"(id={movie_id}, requester={requester_display})"
    )


if __name__ == "__main__":
    main()
