# Ortflix Integrations

[![Lint - Ortflix Integrations](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/lint.yml/badge.svg)](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/lint.yml)
[![Security - Ortflix Integrations](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/security.yml/badge.svg)](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/security.yml)
[![Tag & Publish - Integrations](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/release.yml/badge.svg)](https://github.com/okdesign21/ortflix-integrations-bot/actions/workflows/release.yml)

Integrations service for Overseerr/Jellyseerr notifications, Telegram approvals, and media automations.

## Architecture

`bot.py` is the application entrypoint and the integration modules under `integrations/` handle webhook routing and Telegram callback flow:

- `integrations/overseerr_integration.py` — Overseerr webhook handling and API enrichment
- `integrations/media_integrity_integration.py` — media corruption webhook handling
- `integrations/radarr_automation.py` — Radarr webhook event-to-script dispatch
- `integrations/sonarr_automation.py` — Sonarr webhook event-to-script dispatch
- `integrations/tautulli_automation.py` — Tautulli webhook event-to-script dispatch
- `integrations/telegram_callbacks.py` — callback queue and interactive Telegram actions

Automation scripts live under `scripts/` and read static defaults from `config/bot_settings.yaml`, with Docker-friendly overrides from a mounted bot settings file.

## Features

- FastAPI webhook service for Seerr and optional media-integrity payloads
- Telegram notifications for `MEDIA_PENDING`, `MEDIA_AVAILABLE`, `MEDIA_FAILED`, and related types
- **Request quality profile** on pending, failed, and available notifications (resolved via Seerr API when the webhook includes a request id)
- **`MEDIA_AVAILABLE` for movies:** optional **downloaded file quality** and **on-disk folder** via the Radarr API (`tmdbId` in the webhook `media` object)
- Inline approve/decline for pending requests
- Operator-triggered manual actions via Telegram command `/actions`
- `GET /health` and typed Pydantic models

## Webhook Setup

Use the setup guide for webhook wiring and ownership decisions:

- See `docs/WEBHOOK_SETUP.md`

Quick rule:

1. Request/import/add/upgrade events → the integrations bot.
2. Playback/session events (e.g. Tautulli "watched") → the integrations bot via webhook (Tautulli Webhook agent, not Script agent).

## Quick Start

```bash
git clone https://github.com/okdesign21/ortflix-integrations.git
cd ortflix-integrations
cp .env.example .env
./run-local.sh
```

The bot starts its FastAPI server on `WEBHOOK_HOST:WEBHOOK_PORT` (default
`0.0.0.0:7777`) and starts Telegram polling for callback buttons and `/actions`.
For configuration, continue with [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
For webhook wiring, continue with [docs/WEBHOOK_SETUP.md](docs/WEBHOOK_SETUP.md).

## Bot Settings File

Large static automation defaults now live in `config/bot_settings.yaml`.

For local development, use the ignored operator override file:

- `config/bot_settings.local.yaml`

For Docker or compose deployments, the preferred mounted override file is:

- `/config/bot_settings.yaml`

The loader merges settings in this order:

1. Bundled defaults from `config/bot_settings.yaml`
2. Optional explicit path from `ORTFLIX_BOT_SETTINGS_FILE`
3. Otherwise, the first available automatic override: `config/bot_settings.local.yaml`
   or `/config/bot_settings.yaml`
4. Per-setting environment variable overrides where supported by a script

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the complete file precedence,
section reference, Docker/Kubernetes mounts, and local override workflow.

Use the settings file for larger data and shared defaults such as media folders, language maps, HDR routing keywords, Letterboxd users, Plex title-check defaults, and repeated tag labels. Keep secrets and runtime wiring in environment variables or Docker secrets.

## Environment Variables

Environment variables remain supported for runtime wiring, secrets, and targeted overrides. For large static script settings, prefer `bot_settings.yaml` instead of expanding `.env`.

### Core

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TELEGRAM_TOKEN` | Yes | - | Telegram bot token from BotFather |
| `OVERSEERR_API_KEY` | Yes | - | Overseerr/Seerr API key |
| `TELEGRAM_CHAT_ID` | Yes | - | Telegram chat/user ID |
| `OVERSEERR_URL` | No | `shared.services.overseerr.url` | Overseerr base URL, or set `OVERSEERR_HOST` + `OVERSEERR_PORT` instead |
| `SONARR_URL` | No | `shared.services.sonarr.url` | Sonarr base URL, or set `SONARR_HOST` + `SONARR_PORT` instead |
| `WEBHOOK_HOST` | No | `0.0.0.0` | Bind host for FastAPI |
| `WEBHOOK_PORT` | No | `7777` | Webhook service port |
| `WEBHOOK_TOKEN` | Yes* | - | Webhook auth token required by default |
| `ALLOW_INSECURE_WEBHOOKS` | No | `false` | Explicitly allow no webhook token on trusted private networks only |
| `RADARR_URL` | No | `shared.services.radarr.url` | Radarr base URL (no trailing slash), or `RADARR_HOST` + `RADARR_PORT` — same rules as `OVERSEERR_URL` |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

### Feature Keys

| Variable | Default | Used For |
| --- | --- | --- |
| `RADARR_API_KEY` | - | Radarr webhook enrichment and Radarr movie automation scripts |
| `SONARR_API_KEY` | - | Sonarr routing and tagging scripts |
| `DISCORD_WEBHOOK_URL` | - | Letterboxd log links posted to Discord on watched event |
| `LETTERBOXD_USERS` | - | Comma-separated Plex usernames to post Letterboxd links for |
| `RADARR_SCRIPT_TIMEOUT` | `180` | Radarr automation script timeout |
| `SONARR_SCRIPT_TIMEOUT` | `180` | Sonarr automation script timeout |
| `TAUTULLI_SCRIPT_TIMEOUT` | `180` | Tautulli automation script timeout |
| `MANUAL_ACTION_TIMEOUT` | `600` | Timeout for Telegram-triggered manual scripts |
| `MANUAL_ACTION_OUTPUT_LIMIT` | `3500` | Max output chars returned to Telegram |
| `AUDIO_LANG_MOVIES_DIRS` | `/movies,/movies-hdr` | Bulk audio-language fixer; prefer `config/bot_settings.yaml` for shared defaults |
| `PLEX_URL` | `shared.plex.url` | Shared Plex base URL for Telegram/manual actions and audio-language post-processing |
| `PLEX_TOKEN_FILE` | `shared.plex.token_file` | Shared Plex token file (Docker compose) |
| `PLEX_TOKEN` | - | Shared Plex token env fallback (k8s/manual runs) |
| `PLEX_SECTION_ID` | `1` | Movies Plex library section ID for audio-language script |
| `PLEX_TV_SECTION_ID` | `2` | TV Plex library section ID for audio-language script |
| `KOMETA_ASSET_DIR` | `/opt/kometa/config/assets/Movies_Shows` | Asset root for `scripts/check_plex_titles.py` |
| `KOMETA_EXCEPTION_MAPPINGS` | `/opt/kometa/tools/asset-organizer/exception_mappings.json` | Optional mappings file for `scripts/check_plex_titles.py` |
| `RADARR_SDR_ROOT` | `/movies` | Radarr HDR routing script |
| `RADARR_HDR_ROOT` | `/movies-hdr` | Radarr HDR routing script |
| `RADARR_HDR_PROFILE_KEYWORDS` | `hdr` | Radarr HDR routing script |
| `SONARR_AUDIO_DEFAULT_LANG` | `eng` | Sonarr audio fixer |
| `SONARR_SDR_ROOT` | `/tv` | Sonarr HDR routing script |
| `SONARR_HDR_ROOT` | `/tv-hdr` | Sonarr HDR routing script |
| `SONARR_HDR_PROFILE_KEYWORDS` | `hdr` | Sonarr HDR routing script |

`WEBHOOK_PATH` may appear in older `.env` files, but the current server registers
fixed routes under `/api/v1/webhooks/` and does not use that variable. Configure the
full endpoint URL in each source application.

Core keys for notifications and approvals: `TELEGRAM_TOKEN`, `OVERSEERR_API_KEY`, and `TELEGRAM_CHAT_ID`.
Radarr automation uses `RADARR_API_KEY` and `RADARR_URL`; Sonarr automation uses
`SONARR_API_KEY` and `SONARR_URL`. Tautulli watched automation uses `RADARR_API_KEY`
and optionally posts Letterboxd links through `DISCORD_WEBHOOK_URL` for usernames in
`LETTERBOXD_USERS`. Set `OVERSEERR_URL`, `RADARR_URL`, or `SONARR_URL` only when you
need an environment override; otherwise use `config/bot_settings.yaml`.

Seerr must be reachable for profile enrichment. Radarr is only used to decorate movie `MEDIA_AVAILABLE` messages; pending and failed flows do not call Radarr.

## API Endpoints

- `GET /health`
- `POST /api/v1/webhooks/overseerr`
- `POST /api/v1/webhooks/seerr` (alias)
- `POST /api/v1/webhooks/media-check`
- `POST /api/v1/webhooks/radarr`
- `POST /api/v1/webhooks/sonarr`
- `POST /api/v1/webhooks/tautulli`

By default startup requires `WEBHOOK_TOKEN`.
Only for trusted private networks, you may set `ALLOW_INSECURE_WEBHOOKS=true` to run without token.
When token is set, send it in `x-webhook-token` header.

All webhook handlers return JSON. Typical responses are:

- `200` with `status: accepted` when automation scripts are scheduled.
- `200` with `status: ignored` when the event is valid but has no configured script.
- `200` with `status: ok` for accepted media-integrity notifications.
- `400` for invalid JSON or invalid media-check notification types.
- `401` for a missing or incorrect token when `WEBHOOK_TOKEN` is configured.
- `422` for a missing event type or invalid payload shape.

See [docs/WEBHOOK_SETUP.md](docs/WEBHOOK_SETUP.md) for source-specific payloads,
event maps, and test commands.

## Telegram Manual Actions

Run `/actions` in the configured chat to open an interactive menu for manual scripts.

Current actions:

1. `check_plex_titles.py --summary` (asset-folder audit)
2. `audio_fix_radarr.py` dry run
3. `audio_fix_radarr.py --apply` live changes

Only the configured `TELEGRAM_CHAT_ID` can trigger these actions.
Manual actions use the same `PLEX_URL` / `PLEX_TOKEN_FILE` / `PLEX_TOKEN`
configuration as the rest of the bot. Use `config/bot_settings.yaml` to override
default Plex and service URLs per deployment.

## Development

```bash
./run-local.sh test
./run-local.sh lint
./run-local.sh format
```

### Webhook Test

```bash
curl -X POST http://localhost:7777/api/v1/webhooks/overseerr \
  -H "Content-Type: application/json" \
  -H "x-webhook-token: your_webhook_token" \
  -d '{"notification_type":"MEDIA_PENDING","subject":"Test","request":{"request_id":"123"}}'
```

Webhook authentication is optional at the route level: when `WEBHOOK_TOKEN` is set,
send the matching `x-webhook-token` header; when it is unset, requests are accepted.
Startup still requires `WEBHOOK_TOKEN` unless `ALLOW_INSECURE_WEBHOOKS=true` is explicitly
enabled for a trusted private network.

Media-integrity callback details are stored in a bounded in-memory cache. The cache is
process-local, so details are lost when the bot restarts and are not shared between
multiple bot replicas. Use sticky routing or a shared state store if callbacks must work
across replicas.

## Docker

```bash
docker build -t ortflix-bot-addons .
docker run --rm \
  -e TELEGRAM_TOKEN="your_token" \
  -e OVERSEERR_API_KEY="your_key" \
  -e TELEGRAM_CHAT_ID="your_chat_id" \
  -e WEBHOOK_TOKEN="your_webhook_token" \
  -e RADARR_API_KEY="your_radarr_key" \
  -p 7777:7777 \
  ortflix-bot-addons
```

The image intentionally runs as root because automation features may need to inspect
or modify mounted media files. Mount the media and configuration paths required by
your scripts, and use Docker secrets for tokens and API keys where possible. The
container entrypoint is `python -m bot`.

Use `config/bot_settings.yaml` for default service URLs and Plex settings in Docker
deployments. Keep environment variables for secrets and per-container overrides only.

For custom host layouts, mount your media roots and set `AUDIO_LANG_MOVIES_DIRS`, `RADARR_SDR_ROOT`, and `RADARR_HDR_ROOT` to match your folder structure.

## CI/CD

- `lint.yml` — lint and formatting checks
- `security.yml` — secret and vulnerability scans
- `release.yml` — tag-driven package/image publish and GitHub release

The release workflow runs on tags matching `v*`. For the planned release, create
`v*.*.*`; setuptools-scm will publish package version and the Docker workflow
will publish the tagged image plus `latest`.

## Related Projects

- Main stack: [`ortflix`](https://github.com/okdesign21/ortflix)
- Kometa and Tautulli automation: [`ortflix-costume`](https://github.com/okdesign21/ortflix-costume)

## License

MIT. See [`LICENSE`](LICENSE).
