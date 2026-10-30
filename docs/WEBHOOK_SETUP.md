# Webhook Setup Guide

This guide shows how each webhook should be wired in the integrations service.

## Ownership Matrix

| Event Type | Source | Execute In | Purpose |
| --- | --- | --- | --- |
| Request lifecycle (`MEDIA_PENDING`, `MEDIA_AVAILABLE`, `MEDIA_FAILED`) | Overseerr/Seerr | integrations bot | Request notifications and approval flow |
| Movie import/add/upgrade routing or tagging | Radarr | integrations bot | Movie automation at import time |
| Series import/add/upgrade routing or tagging | Sonarr | integrations bot | Series automation at import time |
| Playback watched/progress/stream sessions | Tautulli | integrations bot (via Tautulli Webhook agent) | Playback-derived tagging and notifications |
| Media integrity scanner output | Custom scanner/cron | integrations bot | Corruption alerts and follow-up actions |

## Setup Order

1. Configure Overseerr/Seerr notifications.
2. Add Radarr and Sonarr webhooks if you use movie or series automation.
3. Add Tautulli only for playback-driven events.
4. Add media integrity webhooks if you run corruption checks.

## Prerequisites

1. Bot service reachable at `http://<host>:7777` or internal DNS.
2. Set `WEBHOOK_TOKEN` in the bot environment for authenticated calls. Every webhook
  accepts the token when configured; startup requires it unless
  `ALLOW_INSECURE_WEBHOOKS=true` is explicitly enabled on a trusted private network.
3. API keys available in bot env or secrets (`OVERSEERR_API_KEY`, `RADARR_API_KEY`, `SONARR_API_KEY`).
4. For Plex re-analysis after audio fixes: `PLEX_TOKEN_FILE` (Docker) or `PLEX_TOKEN` env var (k8s).

## Key Matrix

| Feature | Required Keys |
| --- | --- |
| Overseerr notifications and approvals | `TELEGRAM_TOKEN`, `OVERSEERR_API_KEY`, `TELEGRAM_CHAT_ID` |
| Radarr movie automation | `RADARR_API_KEY`, optional `RADARR_URL` |
| Sonarr series automation | `SONARR_API_KEY`, optional `SONARR_URL` |
| Tautulli playback automation | `RADARR_API_KEY`; optional `DISCORD_WEBHOOK_URL`, `LETTERBOXD_USERS` |
| Media-integrity alerts | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`; optional token auth |

Core keys cover notifications and approvals; Radarr and Sonarr each use their own API key.

## Bot Endpoints

1. `POST /api/v1/webhooks/overseerr`
2. `POST /api/v1/webhooks/seerr` (alias)
3. `POST /api/v1/webhooks/radarr`
4. `POST /api/v1/webhooks/sonarr`
5. `POST /api/v1/webhooks/tautulli`
6. `POST /api/v1/webhooks/media-check`

Header auth:

1. `x-webhook-token: <WEBHOOK_TOKEN>`

Authentication is enabled whenever `WEBHOOK_TOKEN` has a non-empty value. Send the
header on every request in that mode. Empty or missing token values allow route-level
requests, but normal application startup rejects that configuration unless
`ALLOW_INSECURE_WEBHOOKS=true` is set.

## Overseerr/Seerr

1. Open webhook or notifications settings.
2. Set the webhook URL to either `http://<bot-host>:7777/api/v1/webhooks/overseerr` or `http://<bot-host>:7777/api/v1/webhooks/seerr`.
3. Enable `MEDIA_PENDING`, `MEDIA_AVAILABLE`, and `MEDIA_FAILED`.
4. Add the token header if enabled.
5. Send a test event and confirm the bot logs and Telegram output.

## Radarr

1. Open Settings -> Connect -> Webhook.
2. Set the URL to `http://<bot-host>:7777/api/v1/webhooks/radarr`.
3. Enable `On Movie Added`, `On File Import`, and `On File Upgrade` if you use all movie scripts.
4. Add the `x-webhook-token` header if enabled.
5. The bot executes Radarr scripts from the baked-in `scripts/` folder.

Script map:

1. `MovieAdded` (`On Movie Added` in Radarr) -> `scripts/route_hdr_on_import_radarr.py`
2. `Download` (`On File Import` and `On File Upgrade` in Radarr) -> `scripts/tag_overseerr_requester_radarr.py`, `scripts/audio_fix_radarr.py`
3. Radarr does not send `FileImport` or `Upgrade` as `eventType`; upgrades still arrive as `Download` (with `isUpgrade: true` in the payload).
4. Other Radarr event names are accepted but ignored unless added to the event map.

Radarr sends its native JSON payload. The bot reads `eventType` (or `event`) and
uses values from `movie` and `movieFile` to populate script environment variables.
For a local smoke test:

```bash
curl -X POST http://localhost:7777/api/v1/webhooks/radarr \
  -H "Content-Type: application/json" \
  -H "x-webhook-token: ${WEBHOOK_TOKEN}" \
  -d '{"eventType":"MovieAdded","movie":{"id":10,"tmdbId":123,"title":"Test Movie","year":2024,"path":"/movies/Test Movie"}}'
```

Useful env vars:

1. `RADARR_SCRIPT_TIMEOUT`
2. `RADARR_SDR_ROOT`, `RADARR_HDR_ROOT`, `RADARR_HDR_PROFILE_KEYWORDS`
3. `AUDIO_LANG_MOVIES_DIRS`, `PLEX_URL`, `PLEX_TOKEN_FILE`, `PLEX_SECTION_ID`

## Sonarr

1. Open Settings -> Connect -> Webhook.
2. Set the URL to `http://<bot-host>:7777/api/v1/webhooks/sonarr`.
3. Add the `x-webhook-token` header if enabled.
4. Enable the series events you want to route.

Script map:

1. `SeriesAdd` (`On Series Add` in Sonarr) -> `scripts/route_hdr_on_import_sonarr.py`
2. `Download` (`On Import` and `On Upgrade` in Sonarr) -> `scripts/tag_overseerr_requester_sonarr.py`, `scripts/audio_fix_sonarr.py`
3. Sonarr does not send `Import` or `Upgrade` as `eventType`; upgrades still arrive as `Download` (with `isUpgrade: true` in the payload).
4. Other Sonarr event names are accepted but ignored unless added to the event map.

Sonarr sends its native JSON payload. The bot reads `eventType` (or `event`) and
uses values from `series` and `episodeFile` to populate script environment variables.
For a local smoke test:

```bash
curl -X POST http://localhost:7777/api/v1/webhooks/sonarr \
  -H "Content-Type: application/json" \
  -H "x-webhook-token: ${WEBHOOK_TOKEN}" \
  -d '{"eventType":"Download","series":{"id":20,"tvdbId":456,"title":"Test Show","year":2024,"path":"/tv/Test Show"},"episodeFile":{"path":"/tv/Test Show/Season 01/episode.mkv"}}'
```

Useful env vars:

1. `SONARR_SCRIPT_TIMEOUT`
2. `SONARR_AUDIO_DEFAULT_LANG`
3. `SONARR_SDR_ROOT`, `SONARR_HDR_ROOT`, `SONARR_HDR_PROFILE_KEYWORDS`

## Configure Radarr/Sonarr/Seerr Webhooks As Code

Instead of the UI steps above, the Radarr and Sonarr webhook connections and the Seerr webhook
agent can be declared in `config/arr_webhooks.yaml` and applied through their APIs:

```bash
python scripts/configure_arr_webhooks.py            # dry run, prints the diff
python scripts/configure_arr_webhooks.py --apply    # writes changes
```

1. Only connections named in the file are managed; other connections (Plex, Telegram, custom
   scripts) are never changed or deleted. Use `--config PATH` for a different file and
   `--app radarr|sonarr|seerr|all` to limit the run. Apps absent from the file are skipped.
2. `RADARR_API_KEY`/`SONARR_API_KEY`/`OVERSEERR_API_KEY` (or their `_FILE` forms) and the
   service URLs follow the same resolution as the other scripts.
3. Seerr has one webhook agent: declare `enabled`, `embed_poster`, `url` and `events`
   (notification type names such as `MEDIA_PENDING`). `json_payload_file` is read relative to the
   config file; omit it to keep the existing payload. Other Seerr options are preserved.
4. If `WEBHOOK_TOKEN` (or `WEBHOOK_TOKEN_FILE`) is set, an `x-webhook-token` header is configured.
   An existing header that the settings do not declare is reported as a difference.
5. URLs, header values and API keys are never printed; changed URLs are reported as
   `url: changed`.
6. The committed file was derived from the live server (no auth header); review the first dry run
   before applying.

## Tautulli

Use the **Webhook** notification agent (not the Script agent) so events route through the bot.

1. Tautulli → Settings → Notification Agents → Add → **Webhook**
2. Webhook URL: `http://<bot-host>:7777/api/v1/webhooks/tautulli`
3. Method: POST
4. Trigger: **Watched** ✓
5. Under **Data → JSON Data**, paste this body:

```json
{
  "event":          "watched",
  "themoviedb_id":  "{themoviedb_id}",
  "imdb_id":        "{imdb_id}",
  "title":          "{title}",
  "year":           "{year}",
  "username":       "{username}"
}
```

6. Add the token header if enabled: `X-Webhook-Token: <WEBHOOK_TOKEN>`
7. Test → Save

Script map:

1. `watched` → `scripts/tag_radarr_watched_tautulli.py`
2. Other Tautulli event values are accepted but ignored.

Required env vars in bot:

1. `RADARR_API_KEY`
2. `DISCORD_WEBHOOK_URL` (optional — posts Letterboxd log link to Discord)
3. `LETTERBOXD_USERS` (optional — comma-separated Plex usernames to post links for)

The Tautulli handler accepts `event`, `trigger`, or `notification_type` as the event
field and normalizes case and spaces. It forwards TMDb/IMDb IDs, title, year, and
username to the script when present.

## Media Integrity

Send corruption reports to `POST /api/v1/webhooks/media-check`:

```json
{
  "notification_type": "CORRUPTION_DETECTED",
  "summary_message": "3 corrupted files detected",
  "count": 3,
  "files": [
    {"path": "/movies/example/movie.mkv", "size": "1.2GB", "error": "Container error"}
  ]
}
```

Use the `x-webhook-token` header when `WEBHOOK_TOKEN` is configured. A report with
`count: 0` returns `{"status":"ok"}` without sending a Telegram alert. A non-zero
report is cached for the **View Details** Telegram button.

The callback cache is bounded and process-local. Entries disappear when the bot
restarts and are not shared between replicas. Use sticky routing or a shared state
store if callbacks must work after restarts or across multiple bot instances.

Smoke test:

```bash
curl -X POST http://localhost:7777/api/v1/webhooks/media-check \
  -H "Content-Type: application/json" \
  -H "x-webhook-token: ${WEBHOOK_TOKEN}" \
  -d '{"notification_type":"CORRUPTION_DETECTED","summary_message":"Test alert","count":0,"files":[]}'
```

## Verification

1. Each source app can reach the bot URL from its network.
2. The token header matches in both places, or insecure mode is explicitly enabled for a trusted network.
3. Test events return HTTP 200; invalid/missing event fields return HTTP 400 or 422 as expected.
4. Bot logs show the event type and downstream action.
5. The expected Telegram message, tag, folder action, or script execution occurs.

For a complete local check from the repository root:

```bash
../.venv/bin/ruff check .
../.venv/bin/ruff format --check .
../.venv/bin/python -m pytest -q
docker build -t ortflix-bot-addons:local -f dockerfile .
```

## Manual Operator Actions

The bot also supports interactive, manual script execution from Telegram:

1. In Telegram, send `/actions` to the bot from the configured `TELEGRAM_CHAT_ID`.
2. Pick an action from the inline keyboard.
3. Wait for completion output (stdout/stderr summary is posted back in chat).
4. Manual actions reuse the same shared service config as the webhook scripts (`PLEX_URL`,
   `PLEX_TOKEN_FILE`, `shared.services.*.url`, and mounted Kometa asset paths when needed).

Default manual actions:

1. Plex asset-folder check summary (`scripts/check_plex_titles.py --summary`)
2. Radarr audio language fix dry run (`scripts/audio_fix_radarr.py`)
3. Radarr audio language fix apply (`scripts/audio_fix_radarr.py --apply`)

## Troubleshooting

1. `401 Invalid webhook token`: token missing or mismatched.
2. `422 Missing event type`: webhook template is wrong.
3. No action but 200: event is accepted but no script is mapped.
4. Script timeout: increase `RADARR_SCRIPT_TIMEOUT`, `SONARR_SCRIPT_TIMEOUT`, or `TAUTULLI_SCRIPT_TIMEOUT`.
5. Docker API socket unavailable on macOS: start Colima with `colima start`, select the `colima` context with `docker context use colima`, and verify with `docker info`.
6. Docker build fails at `COPY *.py .`: use the repository Dockerfile version with the directory destination `COPY *.py ./`.
