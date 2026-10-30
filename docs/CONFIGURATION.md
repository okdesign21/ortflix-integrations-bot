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

- `radarr.movies_dirs`: Radarr bulk scan directories and allowed webhook media roots.
- `sonarr.tv_dirs`: allowed Sonarr webhook media roots; empty by default (fail closed).
- `plex_section_id` under Radarr or Sonarr: Plex library section IDs.
- `default_language`: fallback ISO-639-2 language code.
- `skip_folders`: folder names skipped by Radarr bulk mode.
- `webhook_extensions`, `bulk_extensions`, and `supported_extensions`: allowed media formats.
- `language_name_map`: Sonarr language-name to ISO-code mapping.

Prefer local overrides for media paths and skip lists.

Webhook audio scripts require absolute, readable regular files with a supported
extension inside a configured library root. Validation resolves symlinks, rejects
traversal and outside-root targets, and passes the canonical path to media tools.
Failures exit nonzero with a non-sensitive stderr message before probing, editing,
or Plex analysis. Test events do not process files and still work without roots.

Set roots using container-visible paths in your operator override, for example:

```yaml
audio_fix:
  radarr:
    movies_dirs: [/movies, /movies-hdr]
  sonarr:
    tv_dirs: [/tv, /tv-hdr]
```

Only include directories actually intended for this bot's media processing.
`AUDIO_LANG_MOVIES_DIRS` and `AUDIO_LANG_TV_DIRS` provide comma-separated environment
overrides. **Before enabling Sonarr audio processing, configure its TV roots**;
the bundled empty list deliberately does not authorize a guessed deployment path.
Mounts and ownership remain deployment controls. Path validation is not protection
against concurrent filesystem replacement by another writer; do not grant
untrusted processes write access to these roots. Radarr bulk mode retains its
separate configured-directory scanning behavior; this guard covers webhook input.

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

#### User map

Watched tags use the Plex username, requested tags the Overseerr one. `user_map.json`
maps each Plex username to the tag name for that person so both match:

```json
{"users": {"chen": {"tag": "chencoh2", "manual": true}}}
```

- `"manual": true` locks an entry; automatic sync never changes it.
- Username keys are case-insensitive but preserve punctuation; tag values are
  slugified for Radarr/Sonarr. For example, `sam_one` and `sam-one` remain
  distinct map identities even though both produce the `sam-one` tag by default.
- If an older map already slugified punctuation in a username key, restore that
  key to the exact Plex username before relying on its mapping.
- `"nickname"` is an optional label for humans; nothing uses it yet.
- When a user is not in the map, the bot matches them to Overseerr (Plex username or
  email, via Tautulli) and adds them as an automatic entry. This needs `TAUTULLI_URL`
  and `TAUTULLI_API_KEY`; without them the user's own name is used and nothing is saved.
- The file lives in `$ORTFLIX_USER_MAP_DIR`, else `/config` when mounted, else `config/`.

Manage it with `scripts/map_users.py` (also available as Telegram manual actions):

```
python scripts/map_users.py show
python scripts/map_users.py sync [--apply]
python scripts/map_users.py set PLEX TAG [--apply]
python scripts/map_users.py nickname PLEX [NAME] [--apply]
python scripts/map_users.py remove PLEX [--apply]
```

Changes are a dry run unless `--apply` is given. Changing the map affects future tags
only; existing Radarr/Sonarr tags are not renamed.

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
The native launcher delegates this validation to Python. Invalid explicit files
exit executable startup with status 1 and an actionable stderr message, without
an uncaught traceback; the loader still raises a ValueError-compatible exception.
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
known supported credentials and recognized credential-bearing URL/header fields
in rendered messages and tracebacks while preserving their existing log format.
Credentials loaded from secret files are remembered for redaction without reading
those files again during logging. Environment values and URL-encoded known values
are also covered.

Executable startup routes Uvicorn error/access logs through those sanitized root
handlers and disables Uvicorn's default logging reconfiguration. ASGI and lifespan
exception tracebacks therefore use the same redaction path.

Automatic script output is redacted before logger calls. Manual-action output is
redacted before truncation and presentation. Launch failures and redownload errors
use generic chat summaries; upstream HTTP error bodies are not copied into API
exceptions. Useful non-sensitive script output remains available to operators.

Redaction is defense in depth, not a guarantee that arbitrary private text can be
recognized. Do not deliberately print secrets or sensitive payloads. Deployments
adding logging handlers after startup or separate dependency log sinks must apply
equivalent sanitization. Existing historical logs and chat messages are not
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

## Build and publication security

### Python build tools

`build-constraints.txt` is the single source for approved exact versions of pip,
setuptools, wheel, the package build frontend, and setuptools-scm. Update those
pins there, not in the Dockerfile or workflow commands. The package's
`pyproject.toml` retains application dependencies and build compatibility/security
minimums; it is not a duplicate list of exact tool versions.

The Docker builder and CI first upgrade pip using `--constraint build-constraints.txt`, then
use the upgraded pip's `--build-constraint` support for isolated builds. Lint/test
installs also use ordinary constraints. The release build frontend receives both
`PIP_CONSTRAINT` and `PIP_BUILD_CONSTRAINT` as absolute paths to the same file, so
its temporary backend environments and nested builds follow the selected versions.
Build isolation remains enabled.

For the shared workspace interpreter, the equivalent install sequence is:

```bash
python -m pip install --upgrade --constraint build-constraints.txt pip
python -m pip install --constraint build-constraints.txt --build-constraint build-constraints.txt ".[dev]"
```

Use the canonical homeserver virtual environment for local Python operations.
These constraints pin the named build tools, not every transitive or runtime
dependency; they are not a complete application lockfile. Constraints do not
install a package unless it is requested or required. Keep the constraints file
available during builds; it is explicitly allowed in the Docker build context.

Debian package versions and updates remain separate OS dependencies. The image
uses an explicit Trixie base and applies available package updates, but that does
not fix advisories for which Trixie has no patched package. No Trivy severity,
unfixed-finding, or scanner-error gate is relaxed by build-tool centralization.
A fresh image build and scan are still required to verify the vulnerability count.

### Runtime image

The builder stage uses the approved toolchain to build the application and its
runtime dependencies into wheels. The final stage installs those wheels offline,
then removes pip, setuptools, wheel, the temporary wheelhouse, and ensurepip's
bundled installer. Build tools and constraints remain builder/CI inputs, not runtime
requirements. Rebuild the image to change dependencies rather than installing
packages into a running container. This separation removes unnecessary toolchain
copies, including dependencies vendored inside pip; it is not a CVE suppression.

Both image scanning and publication first require an offline smoke check of the
exact candidate with no network or host media mounts. The check verifies that
build-tool modules are absent, imports the bot using synthetic configuration, and
generates a tiny synthetic audio file for FFmpeg/ffprobe and MKV language-edit
checks. It does not contact Telegram, Plex or other services. Use the same Python
module launch context as the production entrypoint:

```bash
docker run --rm --network none --entrypoint python ortflix-bot-addons:scan -m scripts.check_runtime_image
```

These native checks still need to pass in CI; local source tests mock media tools.
The media APT package versions and runtime identity are unchanged. Removing build
tools does not patch libxml2, FFmpeg or other remaining OS libraries, and the
blocking Trivy scan may still fail. Do not treat an unbuilt/unscanned source change
as proof that all 213 reported package/CVE findings are resolved.

### Build context and gates

The Docker context is an allowlist: runtime entrypoint modules, Python sources
under `integrations/` and `scripts/`, package metadata/build constraints, the README, and bundled
`config/bot_settings.yaml`. Local env files, operator overrides, private data files,
tests, caches, and unrelated root Python files are excluded. Mount operator settings
and credentials at runtime rather than copying them into an image. Python source
can still contain hardcoded secrets; build exclusions do not replace secret checks.

Linux CI enables `RUN_DOCKER_BUILD_TESTS=1`. Its disposable scratch-image test uses
synthetic files to check both excluded private inputs and included runtime inputs.
For a local opt-in run against an explicitly selected disposable Docker engine:

```bash
RUN_DOCKER_BUILD_TESTS=1 python -m pytest tests/security_build_test.py -q
```

Repository, image and publication-candidate scans fail on HIGH/CRITICAL
vulnerabilities, including unfixed findings, and detected secrets. Scanner errors
are blocking; there is no continue-on-error bypass. PR history scanning runs
without provider verification, and repository/image secret checks also cover the
publication path. Raw scan results go to temporary runner files, not uploaded
artifacts or routine console output. An `always()` step reads each report and adds
a sanitized summary to the GitHub step summary, even when the scan fails. It shows
HIGH/CRITICAL vulnerability IDs, package versions, and secret rule/category plus
file basename and line; it never renders secret matches, source snippets, titles,
or descriptions. Up to 500 vulnerability details and 30 secret metadata entries
are shown, with omitted-entry counts and a distinct vulnerability-ID count. The
213 reported package/CVE findings therefore fit without the previous truncation.
If no report was emitted, the summary says it is unavailable and
points to scanner diagnostics. Do not publish the raw report files because secret
findings can contain sensitive values.

Tag and edge publication use the same reusable workflow. It builds and loads one
candidate, scans that exact local image ID, and only then authenticates, tags, and
pushes the scanned image without rebuilding. Both historical image repository
aliases and the existing release/edge tag conventions remain supported. Image
publication is single-platform, as before; multi-platform promotion needs an
equivalent per-platform scanning design.

Existing findings may block the first stricter CI run. Fix them or explicitly
review a narrowly scoped exception with owner, reason and expiry; never restore
non-blocking exit codes or add blanket exclusions. This change adds no exceptions.
No production image has been scanned or published by the local tests. Native
Docker and registry behavior must pass CI before relying on the new pipeline.

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
