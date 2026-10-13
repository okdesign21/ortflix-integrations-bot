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
