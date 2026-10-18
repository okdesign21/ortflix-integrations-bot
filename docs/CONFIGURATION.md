# Configuration Guide

This guide explains how to configure automation defaults without putting personal
paths or operator-specific choices into the repository's shared defaults.

## Configuration files

The tracked repository file is the general default configuration:

```text
config/bot_settings.yaml
```

Personal or deployment-specific settings should go in one of these override files:

| Location | Use |
| --- | --- |
| `config/bot_settings.local.yaml` | Local development or a checkout on one host. Ignored by Git. |
| `/config/bot_settings.yaml` | Preferred Docker Compose or Kubernetes mounted configuration path. |
| `/config/bot_settings.yml` | Same as above, YAML extension alternative. |
| `ORTFLIX_BOT_SETTINGS_FILE` | Explicit path; takes precedence over automatic candidates. |

## Precedence

The loader deep-merges settings in this order:

1. Tracked defaults from `config/bot_settings.yaml`.
2. If `ORTFLIX_BOT_SETTINGS_FILE` is set, that explicit file is used as the override.
3. Otherwise, the first automatic override found in this order:
   - `config/bot_settings.local.yaml`
   - `/config/bot_settings.yaml` or `.yml`

An explicit `ORTFLIX_BOT_SETTINGS_FILE` path must exist. Nested mappings are merged;
lists and scalar values are replaced by the override value. Keep the override file
focused on the values that differ from the general defaults.

Example local setup:

```bash
cp config/bot_settings.yaml config/bot_settings.local.yaml
# Edit config/bot_settings.local.yaml with host-specific paths and choices.
```

Example explicit setup:

```bash
export ORTFLIX_BOT_SETTINGS_FILE=/path/to/my-bot-settings.yaml
```

## Settings sections

### `shared`

Shared Plex settings and the language-code map used by audio-language scripts.

```yaml
shared:
  plex:
    url: http://localhost:32400
    token_file: /run/secrets/plex_token
  services:
    overseerr:
      url: http://overseerr:5055
    radarr:
      url: http://radarr:7878
    sonarr:
      url: http://sonarr:8989
```

Use `PLEX_TOKEN_FILE` or `PLEX_TOKEN` environment settings for deployment-specific
secret wiring. Do not put a Plex token value in YAML. Telegram manual actions use
the same shared Plex settings. Use `shared.services.*.url` in the config file to
override default service URLs for a deployment.

### `audio_fix`

Controls Radarr and Sonarr audio-language scripts:

- `movies_dirs`: directories scanned by Radarr bulk mode.
- `plex_section_id` under Radarr or Sonarr: Plex library section IDs.
- `default_language`: fallback ISO-639-2 language code.
- `skip_folders`: folder names skipped by Radarr bulk mode.
- `webhook_extensions`, `bulk_extensions`, and `supported_extensions`: allowed media formats.
- `language_name_map`: Sonarr language-name to ISO-code mapping.

Prefer local overrides for media paths and skip lists.

### `hdr_routing`

Controls the root folder and profile keywords used by the Radarr and Sonarr HDR
routing scripts. The root folders must be visible to the bot with the same paths used
by Radarr/Sonarr.

### `overseerr_requester_tagging`

Controls the approved-request query and source tag used by Radarr/Sonarr requester
tagging scripts. `source_tag_label` is normally `overseer`.

### `tautulli_watched`

Controls watched and triage tag suffixes. `letterboxd_users` is a list of Plex
usernames eligible for Letterboxd logging; use an environment override when the list
is deployment-specific.

### `plex_title_check`

Controls the default Kometa asset directory, exception mapping file, Plex libraries,
and GUID prefixes used by `scripts/check_plex_titles.py`.

## Environment variables versus YAML

Use YAML for shared, structured, non-secret defaults:

- Media roots and folder lists.
- Language maps.
- HDR routing roots and keywords.
- Plex library IDs and asset paths.
- Tag labels and static query defaults.

Use environment variables or Docker/Kubernetes secrets for:

- Telegram, Overseerr, Radarr, and Sonarr API keys.
- Webhook authentication tokens.
- Plex tokens.
- Service URLs that differ by deployment.
- Runtime timeouts and output limits.

The environment variables documented in the main [README](../README.md) remain
supported. Script-specific environment variables override corresponding script
defaults where implemented.

## Docker and Kubernetes

### Secret files

The following credentials accept UTF-8 secret files. Leading and trailing
whitespace (including a final newline) is removed from file contents.

| File setting | Environment fallback | Used for |
| --- | --- | --- |
| `TELEGRAM_TOKEN_FILE` | `TELEGRAM_TOKEN` | Main Telegram bot and script notifications |
| `OVERSEERR_API_KEY_FILE` | `OVERSEERR_API_KEY` | Seerr/Overseerr API access |
| `RADARR_API_KEY_FILE` | `RADARR_API_KEY` | Radarr enrichment and automation scripts |
| `SONARR_API_KEY_FILE` | `SONARR_API_KEY` | Sonarr automation scripts |
| `WEBHOOK_TOKEN_FILE` | `WEBHOOK_TOKEN` | Incoming webhook authentication |
| `PLEX_TOKEN_FILE` | `PLEX_TOKEN` | Plex manual actions and audio-language scripts |
| `DISCORD_WEBHOOK_URL_FILE` | `DISCORD_WEBHOOK_URL` | Optional Discord notifications |
| `API_KEY_FILE` | `API_KEY` | Legacy main-bot Overseerr key alias; also accepted by scripts |
| `SEERR_API_KEY_FILE` | `SEERR_API_KEY` | Script-only Overseerr key alias |
| `TELEGRAM_BOT_TOKEN_FILE` | `TELEGRAM_BOT_TOKEN` | Script-only Telegram token alias |

Precedence and failures:

1. If a credential's `*_FILE` variable is set, that file is authoritative, even
  when the corresponding environment value is also set. An empty path, missing
  or unreadable file, invalid UTF-8, or whitespace-only contents is an error.
  There is no environment fallback after a configured-file error.
2. Otherwise the existing environment value is used unchanged. No file settings
  are required for existing environment-only deployments.
3. Alias order remains main bot: `OVERSEERR_API_KEY` then `API_KEY`; automation
  scripts: `OVERSEERR_API_KEY`, `SEERR_API_KEY`, then `API_KEY`. Script Telegram
  notifications prefer `TELEGRAM_BOT_TOKEN` over `TELEGRAM_TOKEN`. Each name uses
  its own file-before-environment rule, so a canonical environment value can
  outrank an alias file.
4. Plex retains its existing default `shared.plex.token_file` lookup before
  `PLEX_TOKEN` when `PLEX_TOKEN_FILE` is unset. A missing default file permits
  the environment fallback; an explicitly configured file does not.

All explicitly configured supported secret files are checked during bot startup,
including optional and shadowed aliases. Errors identify the setting without
including its path or secret contents. Scripts read their files when invoked.
Secret changes require a bot restart; credentials are not hot-reloaded.
`TELEGRAM_CHAT_ID` remains a regular environment setting, not a secret credential.

An environment `WEBHOOK_TOKEN` containing only whitespace is rejected at startup,
even when `ALLOW_INSECURE_WEBHOOKS=true`. An absent or empty environment token is
allowed only with that explicit insecure-mode override. Configured secret files
must always be readable and non-empty; insecure mode does not bypass file errors.

For a locally built image, mount a directory containing the required secret files
read-only (the files must be readable by the container user):

```bash
docker build -t ortflix-bot-addons .
docker run --rm -p 7777:7777 \
  --mount type=bind,src="$(pwd)/secrets",dst=/run/secrets,readonly \
  -e TELEGRAM_TOKEN_FILE=/run/secrets/telegram_token \
  -e OVERSEERR_API_KEY_FILE=/run/secrets/overseerr_api_key \
  -e WEBHOOK_TOKEN_FILE=/run/secrets/webhook_token \
  -e TELEGRAM_CHAT_ID=12345 \
  ortflix-bot-addons
```

Create the files privately before running the container; never commit them. Docker
Compose or Swarm secrets can mount the same files at `/run/secrets/<secret-name>`.
Kubernetes Secret volumes work the same way: set each `*_FILE` variable to its
mounted file path. Use an image containing this feature before changing deployment
settings. This change does not migrate the Ortflix Compose configuration.

### Bot settings mount

Mount the operator override at `/config/bot_settings.yaml`:

```yaml
services:
  bot:
    volumes:
      - ./config/bot_settings.local.yaml:/config/bot_settings.yaml:ro
```

For Kubernetes, mount a ConfigMap at `/config/bot_settings.yaml` and keep
tokens/API keys in Secrets. The bot reads the mounted file when each automation script
starts.

## Logging

`LOG_LEVEL` controls application diagnostics. HTTPX, HTTPcore and Telegram loggers
stay at `WARNING` to avoid verbose credential-bearing request logs, including when
the application uses `DEBUG`. Root logging handlers present at bot startup redact
the configured Telegram token and Telegram Bot API token paths in rendered
messages and tracebacks while preserving their existing log format.

This is Telegram-token protection, not general sanitization of every API response
or script output. Deployments adding logging handlers after startup or separate
dependency log sinks must apply equivalent sanitization. Do not print real tokens
or raw production request logs to test logging. Existing historical logs are not
rewritten, and deploying this fix does not automatically rotate credentials.

## Script lifecycle

Automatic and Telegram-triggered scripts share timeout and cancellation cleanup.
After a timeout or cancellation, the bot requests termination, allows up to five
seconds for graceful shutdown, then force-stops remaining work and reaps the child
before returning the timeout or propagating cancellation.

On Linux/POSIX, scripts start in a separate session and cleanup signals their
process group, including media tools that remain in that group. Detached processes
that create their own session are not covered. On Windows, cleanup stops and reaps
the direct child only; descendant cleanup is not guaranteed. The native POSIX
process-group integration test runs on Linux CI and is skipped on Windows.

Timeouts still use `RADARR_SCRIPT_TIMEOUT`, `SONARR_SCRIPT_TIMEOUT`,
`TAUTULLI_SCRIPT_TIMEOUT` or `MANUAL_ACTION_TIMEOUT`, as appropriate. The extra
cleanup grace period is separate from those execution limits. Output-memory limits
and concurrency control are separate concerns, not provided by timeout cleanup.

## Validation checklist

After changing configuration:

1. Confirm every media path exists inside the bot container.
2. Confirm Radarr/Sonarr use the same mounted path names as the bot.
3. Confirm the bot container can reach Plex via `PLEX_URL` and can read the Plex token.
4. Confirm any Kometa asset paths used by `/actions` are mounted inside the bot container.
5. Run a dry-run action before using an `--apply` action.
6. Send a test webhook from [WEBHOOK_SETUP.md](WEBHOOK_SETUP.md).
7. Check logs for missing files, invalid YAML, or permission errors.

Invalid YAML, a non-object top-level value, or a missing explicit override path causes
the script to fail with an actionable error. Never commit personal override files or
secrets.
