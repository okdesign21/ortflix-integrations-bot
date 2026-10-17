#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tag_radarr_watched_tautulli.py — On Plex "Watched", tag the movie in Radarr and post
a clickable Letterboxd log link to Discord.

Adds tags: "{username}-watched" and "tbd" (creates tags if missing).
Discord message includes deep link into the Letterboxd app and a web fallback.

── Trigger setup (webhook via integrations-bot) ──────────────────────────────
In Tautulli, add a Webhook notification agent (NOT the Script agent):

  Tautulli → Settings → Notification Agents → Add → Webhook
    Webhook URL:   http://integrations-bot:7777/api/v1/webhooks/tautulli
    Method:        POST
    Triggers:      Watched ✓

  Under "Data" → "JSON Data", set the body template to:
    {
      "event":           "watched",
      "themoviedb_id":   "{themoviedb_id}",
      "imdb_id":         "{imdb_id}",
      "title":           "{title}",
      "year":            "{year}",
      "username":        "{username}"
    }

  The bot normalizes the "event" field and dispatches to this script.
  Only movies are tagged (series events are silently ignored by find_movie).

── Env vars (set in integrations-bot container) ──────────────────────────────
  RADARR_API_KEY      (required)
  RADARR_URL          (optional; default: http://radarr:7878)
  DISCORD_WEBHOOK_URL (optional; Letterboxd links posted here when set)
    LETTERBOXD_USERS    (optional; overrides bot_settings.yaml list)
"""

import argparse
from datetime import datetime
from urllib.parse import quote_plus, urlencode

from integration_utils import (
    _env,
    add_tags_to_movie,
    discord_post,
    ensure_tag,
    env_csv_list,
    fail,
    find_movie,
    load_bot_settings,
    radarr_base_url,
    radarr_session,
)
from user_map import resolve_user

# ====== CONFIG ======
BOT_SETTINGS = load_bot_settings()
TAUTULLI_SETTINGS = BOT_SETTINGS["tautulli_watched"]

# List of usernames to post Letterboxd log links for
LETTERBOXD_USERS = [
    user.strip().lower()
    for user in env_csv_list(
        "LETTERBOXD_USERS",
        ",".join(TAUTULLI_SETTINGS["letterboxd_users"]),
    )
    if user.strip()
]
WATCHED_TAG_SUFFIX = str(TAUTULLI_SETTINGS["watched_tag_suffix"]).strip().lower() or "watched"
TRIAGE_TAG_LABEL = str(TAUTULLI_SETTINGS["triage_tag_label"]).strip() or "tbd"


# ====== Letterboxd ======
def letterboxd_log_link(username: str, title: str, year=None) -> None:
    """Post a Letterboxd log link to Discord (clickable) for configured users."""
    if str(username).strip().lower() not in LETTERBOXD_USERS:
        return

    name = f"{title} ({year})" if year else title

    # Deep link into the app
    app_link = "letterboxd://x-callback-url/log?" + urlencode(
        {
            "name": name,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": "plex",
        }
    )

    # Web link
    web_link = f"https://letterboxd.com/search/films/{quote_plus(name)}"

    content = (
        f"🎬 **Log to Letterboxd**\n"
        f"**Movie:** {name}\n"
        f"[Open in Letterboxd app]({app_link})\n"
        f"[Or view on the web]({web_link})"
    )
    discord_post(content)


# ====== Main ======
def main():
    p = argparse.ArgumentParser(
        description="Tag Radarr movie on Plex Watched (via Tautulli) and post to Discord."
    )
    p.add_argument("--themoviedb_id")
    p.add_argument("--imdb_id")
    p.add_argument("--title")
    p.add_argument("--year")
    p.add_argument("--username")
    args = p.parse_args()

    radarr_url = radarr_base_url()
    radarr_api_key = _env("RADARR_API_KEY", required=True)

    s = radarr_session(radarr_api_key)

    try:
        movie = find_movie(
            s,
            radarr_url,
            tmdb_id=args.themoviedb_id,
            imdb_id=args.imdb_id,
            title=args.title,
            year=args.year,
        )
    except Exception as e:
        fail(f"Radarr lookup failed: {e}")

    if not movie:
        fail(
            f"No matching movie in Radarr (tmdb={args.themoviedb_id!r}, "
            f"imdb={args.imdb_id!r}, title={args.title!r}, year={args.year!r})."
        )

    movie_id = movie.get("id")
    if not movie_id:
        fail("Matched Radarr movie missing 'id'.")

    username = args.username or "unknown"
    # Falls back to "unknown" if username has no valid chars (e.g., Japanese)
    watched_tag_label = f"{resolve_user(username)}-{WATCHED_TAG_SUFFIX}"

    try:
        watched_id = ensure_tag(s, radarr_url, watched_tag_label)
        tbd_id = ensure_tag(s, radarr_url, TRIAGE_TAG_LABEL)
        add_tags_to_movie(s, radarr_url, int(movie_id), [watched_id, tbd_id])
    except Exception as e:
        fail(f"Applying tags failed: {e}")

    print(
        f"[Radarr] Tagged '{movie.get('title')}' ({movie.get('year')}) with "
        f"['{watched_tag_label}', '{TRIAGE_TAG_LABEL}'] "
        f"(Radarr id={movie_id}, user={username})"
    )

    # Post to Discord Letterboxd link if configured
    letterboxd_log_link(
        args.username or "",
        movie.get("title"),
        movie.get("year"),
    )


if __name__ == "__main__":
    main()
