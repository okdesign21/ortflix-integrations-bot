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

Credentials also accept `*_FILE` settings for mounted Docker/Kubernetes secrets.
An explicitly set file takes precedence over the matching environment value;
invalid or empty files fail startup rather than falling back. See
[secret files and container examples](docs/CONFIGURATION.md#secret-files) for the
supported variables, alias precedence, and mount requirements.

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
| `AUDIO_LANG_MOVIES_DIRS` | `/movies,/movies-hdr` | Radarr bulk scan and allowed webhook roots; prefer `audio_fix.radarr.movies_dirs` in settings |
| `AUDIO_LANG_TV_DIRS` | Empty (fail closed) | Allowed Sonarr webhook roots; configure `audio_fix.sonarr.tv_dirs` before processing episodes |
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
make trivy-image
```

With GNU Make, Python, Docker and Trivy available on `PATH`, `make trivy-image`
builds `ortflix-bot-addons` and scans it using the CI policy:
HIGH/CRITICAL vulnerabilities (including unfixed) and secrets. It prints only the
sanitized Trivy summary and returns nonzero when the scan fails or reports findings.
Docker and Trivy must be installed and available on `PATH`. Override their commands
when needed, for example `make trivy-image DOCKER=docker.exe TRIVY=trivy.exe` or
`make trivy-image PYTHON=py`. The target and Python wrapper work with GNU Make on
Windows and Linux; the repository's existing helper targets may still require a
POSIX shell.

Exact Python build-tool versions live in [build-constraints.txt](build-constraints.txt).
Docker and CI share those constraints, including isolated package builds. Keep
application dependencies and compatibility minimums in `pyproject.toml`; see
[build-tool configuration](docs/CONFIGURATION.md#python-build-tools).

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

Python build tools are confined to the builder stage; pip, setuptools, wheel and
ensurepip's bundled installer are absent from the final runtime filesystem. CI
requires an offline import/media smoke check before scanning or publishing that
image. Rebuild to change Python dependencies; do not install them into a running
container. OS/media package vulnerabilities remain a separate scan requirement.

### Native Media Runtime

The image uses the digest-pinned official `opensuse/tumbleweed` image and installs
Python 3.14, FFmpeg, MKVToolNix and their runtime libraries together with `zypper`.
There are no copied third-party binaries or locally compiled media packages.
RPM package signatures are checked by the package manager, and the installed
RPM database is retained for vulnerability scanning. Explicit package pins
select FFmpeg 8.1.3, MKVToolNix 102.0 and libxml2 2.15.4, together with updated
Qt, util-linux, systemd, ncurses and ACL libraries.

Current CI validates Linux x86-64 (amd64). Other architectures offered by the
upstream base have not been certified for this application.
Tumbleweed is a rolling distribution. Update the pinned base digest and direct
RPM versions together when refreshing the runtime; mirror rotation can remove
old package revisions and intentionally cause a stale build to fail. Transitive
dependencies are resolved by the vendor package manager. Build with
`--pull --no-cache` to avoid reusing a cached installation layer. The resolved
image, not its source recipe alone, is the security artifact.

Build and runtime stages share the same native package baseline. Only the
builder installs pip and creates a temporary build environment; the final stage
receives installed application packages, not pip, the wheelhouse or build tools.
The bot entrypoint, media mounts and root execution policy are unchanged.

P3-006 remains open until an actual image build, offline smoke check and blocking
Trivy scan pass. The smoke check covers Matroska probe/language edits and MP4
stream-copy language edits, without networking or host volumes:

```bash
docker build --pull --no-cache -t ortflix-bot-addons:scan .
docker run --rm --network none --entrypoint python ortflix-bot-addons:scan -m scripts.check_runtime_image
python scripts/scan_image.py ortflix-bot-addons:scan
```

The local scan command and CI require Trivy to recognize `opensuse-tumbleweed`
and report the Python, FFmpeg, MKVToolNix and libxml2 RPM packages. Missing native
scan coverage fails closed, even when the scanner reports no vulnerabilities.
Fewer findings alone do not establish remediation.
Do not remove package metadata, suppress findings or enable `ignore-unfixed`
to force publication. CI scans and publishes the same candidate image without
rebuilding it; a newer package version alone is not proof of a patched image.

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

## Dependency Maintenance

Security checks build and scan the default branch nightly at 03:00 UTC; they do
not deploy or inspect the live server. Weekly update proposals run Mondays at
04:00 UTC, or manually through the **Weekly Dependency Updates** workflow.

Two fixed branches keep proposals separate: `automation/dependencies-runtime`
updates the base digest and RPM pins together, while
`automation/dependencies-build-tools` updates Python build constraints to stable,
non-yanked releases compatible with Python 3.11 and 3.14. Existing Python and
FFmpeg package streams stay unchanged. Missing packages, unsupported RPM editions
and downgrades fail for manual investigation instead of changing the runtime.

No tracked version/digest changes means no PR. New changes refresh the existing
update PR rather than creating duplicates. Bot-created PRs explicitly dispatch
the existing lint and security workflows, including a blocking history secret
scan of the validated base/head commit range. Dependabot continues weekly application
and GitHub Actions updates, with automatic approval retained; merging is manual.
Required status checks must be configured in the repository's branch rules.

Enable **Settings > Actions > General > Allow GitHub Actions to create and approve
pull requests**. Approval uses the existing `DEPENDABOT_PAT` secret when configured,
otherwise `GITHUB_TOKEN`; the token must be allowed to approve reviews. Scheduled
workflows start after these changes reach the default branch. This source change
does not cancel auto-merge already enabled on an existing PR.

The updater supports read-only discovery without modifying files:

```bash
python scripts/update_dependencies.py --group runtime
python scripts/update_dependencies.py --group build-tools
```

## Related Projects

- Main stack: [`ortflix`](https://github.com/okdesign21/ortflix)
- Kometa and Tautulli automation: [`ortflix-costume`](https://github.com/okdesign21/ortflix-costume)

## License

MIT. See [`LICENSE`](LICENSE).
