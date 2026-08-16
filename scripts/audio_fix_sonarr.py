#!/usr/bin/env python3
"""
audio_fix_sonarr.py — Tag missing/und audio language on imported Sonarr episode files,
then trigger Plex re-analysis so Kometa overlays (resolution, HDR, codec) are current.

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
  The bot injects sonarr_eventtype, sonarr_series_originallanguage,
  sonarr_series_title, and sonarr_episodefile_path from the webhook payload.

── Env vars (set in integrations-bot container) ──────────────────────────────
    PLEX_URL                (optional; default: see bot_settings.yaml)
    PLEX_TOKEN_FILE         (optional; default: see bot_settings.yaml)
    PLEX_TV_SECTION_ID      (optional; default: see bot_settings.yaml)
    SONARR_AUDIO_DEFAULT_LANG (optional; default: see bot_settings.yaml)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (optional; for warning notifications)

── Requirements (installed in integrations-bot image) ────────────────────────
  mkvtoolnix (mkvpropedit)
  ffmpeg
"""

import json
import os
import subprocess
import sys

from integration_utils import (
    env_str,
    load_bot_settings,
    resolve_plex_token,
    resolve_plex_url,
    telegram_post,
)

BOT_SETTINGS = load_bot_settings()
SHARED_SETUP = BOT_SETTINGS["shared"]
SONARR_SETUP = BOT_SETTINGS["audio_fix"]["sonarr"]
LANG_MAP = SHARED_SETUP["audio_language_map"]
LANG_NAME_MAP = SONARR_SETUP["language_name_map"]
SUPPORTED_EXTENSIONS = set(SONARR_SETUP["supported_extensions"])

PLEX_URL = resolve_plex_url(str(SHARED_SETUP["plex"]["url"]))
PLEX_TOKEN_FILE = env_str("PLEX_TOKEN_FILE", SHARED_SETUP["plex"]["token_file"])
PLEX_TV_SECTION_ID = env_str("PLEX_TV_SECTION_ID", SONARR_SETUP["plex_section_id"])

DEFAULT_LANG = (
    env_str("SONARR_AUDIO_DEFAULT_LANG", SONARR_SETUP["default_language"]).strip().lower() or "eng"
)


def _audio_streams(path):
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",
            path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(r.stdout).get("streams", [])
    except Exception:
        return []


def _needs_tag(stream):
    tags = stream.get("tags") or {}
    lang = str(tags.get("language", "") or tags.get("LANGUAGE", "")).strip().lower()
    return not lang or lang in ("und", "none")


def _fix_mkv(path, lang):
    streams = _audio_streams(path)
    args = ["mkvpropedit", path]
    for i, s in enumerate(streams):
        if _needs_tag(s):
            args += ["--edit", f"track:a{i + 1}", "--set", f"language={lang}"]
    if len(args) == 2:
        return True
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode == 0


def _fix_mp4(path, lang):
    streams = _audio_streams(path)
    meta_args = []
    for i, s in enumerate(streams):
        if _needs_tag(s):
            meta_args += [f"-metadata:s:a:{i}", f"language={lang}"]
    if not meta_args:
        return True

    dirname = os.path.dirname(path) or "."
    base = os.path.basename(path)
    tmp = os.path.join(dirname, f".langtmp.{base}")
    cmd = (
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c", "copy", "-map", "0"]
        + meta_args
        + [tmp]
    )
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
    try:
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def _sonarr_language() -> str:
    """Read sonarr_series_originallanguage (ISO 639-1 or name) and map to ISO 639-2."""
    raw = os.getenv("sonarr_series_originallanguage", "").strip().lower()
    if not raw:
        return DEFAULT_LANG
    mapped = LANG_MAP.get(raw) or LANG_NAME_MAP.get(raw)
    if not mapped:
        series = os.getenv("sonarr_series_title", "unknown series")
        telegram_post(
            f"⚠️ **audio_fix_sonarr**: unknown language `{raw}` for **{series}** — "
            f"defaulted to `{DEFAULT_LANG}`. Add it to bot_settings.yaml if wrong."
        )
        return DEFAULT_LANG
    return mapped


def plex_analyze_item(file_path: str) -> None:
    """Find episode in Plex by file path and trigger re-analysis for Kometa overlays."""
    try:
        import urllib.request

        token = resolve_plex_token(PLEX_TOKEN_FILE)
        if not token:
            print("  Plex: no token available (set PLEX_TOKEN_FILE or PLEX_TOKEN)")
            return
        # Search episodes (type=4) in the TV section
        url = f"{PLEX_URL}/library/sections/{PLEX_TV_SECTION_ID}/all?type=4&X-Plex-Token={token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            for media in item.get("Media", []):
                for part in media.get("Part", []):
                    if part.get("file") == file_path:
                        key = item["ratingKey"]
                        req2 = urllib.request.Request(
                            f"{PLEX_URL}/library/metadata/{key}/analyze?X-Plex-Token={token}",
                            method="PUT",
                        )
                        urllib.request.urlopen(req2, timeout=10)
                        print(f"  Plex: triggered analyze for episode ratingKey={key}")
                        return
        print("  Plex: episode not found yet (Plex may not have scanned it yet — that's fine)")
    except Exception as e:
        print(f"  Plex: analyze trigger skipped — {e}")


def main() -> None:
    event = os.getenv("sonarr_eventtype", "")
    if event == "Test":
        print("[audio_fix_sonarr] Test event received — script is working.")
        sys.exit(0)

    if event not in ("Download", "Upgrade"):
        print(f"[audio_fix_sonarr] Skipping event type: {event!r}")
        sys.exit(0)

    file_path = os.getenv("sonarr_episodefile_path", "")
    if not file_path or not os.path.exists(file_path):
        print(f"[audio_fix_sonarr] File not found: {file_path!r}")
        sys.exit(1)

    lang = _sonarr_language()
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    ok = True
    if ext not in SUPPORTED_EXTENSIONS:
        print(f"[audio_fix_sonarr] Unsupported format: {ext!r}")
        sys.exit(0)

    if ext == "mkv":
        ok = _fix_mkv(file_path, lang)
    elif ext == "mp4":
        ok = _fix_mp4(file_path, lang)

    if not ok:
        series = os.getenv("sonarr_series_title", "unknown series")
        telegram_post(
            f"⚠️ **audio_fix_sonarr** failed for **{series}**\nFile: `{os.path.basename(file_path)}`"
        )
        sys.exit(1)

    print(f"[audio_fix_sonarr] Tagged audio language as [{lang}]: {os.path.basename(file_path)}")
    plex_analyze_item(file_path)


if __name__ == "__main__":
    main()
