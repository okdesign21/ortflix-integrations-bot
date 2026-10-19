#!/usr/bin/env python3
"""
audio_fix_radarr.py — Tag missing/und audio language on imported Radarr movie files.

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
  The bot injects radarr_eventtype, radarr_movie_originallanguage, radarr_movie_title,
  radarr_movie_year, and radarr_moviefile_path from the webhook payload.
  Language is read from radarr_movie_originallanguage — works for any language.

── As a one-off bulk fix ──────────────────────────────────────────────────────
    python3 audio_fix_radarr.py           # dry run
    python3 audio_fix_radarr.py --apply   # apply to all files in MOVIES_DIR

    In bulk mode, default language comes from bot_settings.yaml. MKV detection
    uses mkvmerge -J (Matroska-native); ffprobe often still reports empty/und
    after mkvpropedit.

── Env vars (set in integrations-bot container) ──────────────────────────────
        AUDIO_LANG_MOVIES_DIRS  (optional; default: see bot_settings.yaml)  comma-separated
        PLEX_URL                (optional; default: see bot_settings.yaml)
        PLEX_TOKEN_FILE         (optional; default: see bot_settings.yaml)
        PLEX_SECTION_ID         (optional; default: see bot_settings.yaml)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (optional; for warning notifications)

── Requirements (installed in integrations-bot image) ────────────────────────
  mkvtoolnix (mkvpropedit, mkvmerge)
  ffmpeg / ffprobe
"""

import json
import os
import re
import subprocess
import sys

from integration_utils import validate_media_path

# telegram_post is optional when running outside the integrations runtime
try:
    from integration_utils import (
        env_csv_list,
        env_str,
        load_bot_settings,
        resolve_plex_token,
        resolve_plex_url,
        telegram_post,
    )
except ImportError:

    def env_csv_list(name, default_csv):
        raw = os.getenv(name, default_csv)
        return [part.strip() for part in raw.split(",") if part.strip()]

    def env_str(name, default):
        return str(os.getenv(name, default))

    def load_bot_settings():
        import yaml

        path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "config", "bot_settings.yaml")
        )
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def telegram_post(text):
        pass  # no-op in bulk mode

    def resolve_plex_url(default):
        return env_str("PLEX_URL", default).rstrip("/")

    def resolve_plex_token(default_token_file):
        token_file = env_str("PLEX_TOKEN_FILE", default_token_file)
        if os.path.exists(token_file):
            with open(token_file, encoding="utf-8") as handle:
                return handle.read().strip()
        return os.environ.get("PLEX_TOKEN", "").strip()


BOT_SETTINGS = load_bot_settings()
SHARED_SETUP = BOT_SETTINGS["shared"]
RADARR_SETUP = BOT_SETTINGS["audio_fix"]["radarr"]
LANG_MAP = SHARED_SETUP["audio_language_map"]

MOVIES_DIRS = env_csv_list(
    "AUDIO_LANG_MOVIES_DIRS",
    ",".join(RADARR_SETUP["movies_dirs"]),
)
PLEX_URL = resolve_plex_url(str(SHARED_SETUP["plex"]["url"]))
PLEX_TOKEN_FILE = env_str("PLEX_TOKEN_FILE", SHARED_SETUP["plex"]["token_file"])
PLEX_SECTION_ID = env_str("PLEX_SECTION_ID", RADARR_SETUP["plex_section_id"])

# Folder names (not full paths) to skip entirely during bulk mode
SKIP_FOLDERS = set(RADARR_SETUP["skip_folders"])
BULK_EXTENSIONS = set(RADARR_SETUP["bulk_extensions"])
WEBHOOK_EXTENSIONS = set(RADARR_SETUP["webhook_extensions"])
DEFAULT_LANG = str(RADARR_SETUP["default_language"]).strip().lower() or "eng"

DRY_RUN = "--apply" not in sys.argv


def _safe_unlink(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def writable_for_mutate(path) -> bool:
    """Bulk live mode must not call ffmpeg/mkvpropedit if we cannot replace the file."""
    if DRY_RUN or os.environ.get("radarr_eventtype"):
        return True
    if os.access(path, os.W_OK):
        return True
    try:
        st = os.stat(path)
        meta = f"uid={st.st_uid} gid={st.st_gid} mode={oct(st.st_mode)}"
    except OSError as e:
        meta = str(e)
    print(
        f"    ERROR: no write permission ({meta}). "
        "Run --apply as the user that owns the library (match Radarr PUID/PGID / media user), "
        "or fix ownership (e.g. chown) on the movie tree."
    )
    return False


def writable_parent_dir(path) -> bool:
    """Creating a temp file beside the MP4 requires write+execute on the parent directory."""
    if DRY_RUN or os.environ.get("radarr_eventtype"):
        return True
    d = os.path.dirname(path) or "."
    if os.access(d, os.W_OK):
        return True
    print(
        f"    ERROR: no write permission on directory {d!r} "
        "(cannot create temp file next to the MP4)."
    )
    return False


def radarr_language() -> str | None:
    """
    When called by Radarr, radarr_movie_originallanguage is set (e.g. 'ja', 'en').
    Map it to the 3-letter ISO 639-2 code that mkvpropedit/ffmpeg expect.
    Returns None if the env var is absent or unrecognised — caller should warn.
    """
    iso1 = os.environ.get("radarr_movie_originallanguage", "").strip().lower()
    if not iso1:
        return None
    mapped = LANG_MAP.get(iso1, iso1)  # pass through if already 3-letter / unknown code
    if iso1 not in LANG_MAP:
        title = os.environ.get("radarr_movie_title", "unknown title")
        year = os.environ.get("radarr_movie_year", "")
        msg = (
            f"⚠️ **audio_fix_radarr**: unknown language code `{iso1}` for "
            f"**{title}** ({year}) — tagged as-is. Add `{iso1}` to bot_settings.yaml if wrong."
        )
        print(f"  [WARN] {msg}")
        telegram_post(msg)
    return mapped


# ── Core helpers ──────────────────────────────────────────────────────────────


def _matroska_lang_from_mkvmerge_props(props):
    """
    Matroska can store Language (ISO-639-2) and/or LanguageIETF (BCP47).
    ffprobe often omits or maps these poorly; mkvmerge -J matches mkvpropedit.
    """
    if not props:
        return ""
    ietf = props.get("language_ietf") or props.get("language_IETF")
    classic = props.get("language")
    for raw in (ietf, classic):
        if raw is None:
            continue
        v = str(raw).strip()
        if not v:
            continue
        if v.lower() in ("und", "none"):
            continue
        return v
    return ""


def _audio_streams_mkvmerge(path):
    """Return ffprobe-shaped stream dicts for each audio track, or None on failure."""
    r = subprocess.run(
        ["mkvmerge", "-J", path],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    out = []
    for t in data.get("tracks", []):
        if t.get("type") != "audio":
            continue
        props = t.get("properties") or {}
        lang = _matroska_lang_from_mkvmerge_props(props)
        out.append({"tags": {"language": lang}})
    return out


def _audio_streams_ffprobe(path):
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


def audio_streams(path):
    """Audio tracks as list of dicts with tags.language (empty / und ⇒ needs fix)."""
    if path.lower().endswith(".mkv"):
        merged = _audio_streams_mkvmerge(path)
        if merged is not None:
            return merged
    return _audio_streams_ffprobe(path)


def needs_tag(stream):
    tags = stream.get("tags") or {}
    lang = str(tags.get("language", "") or tags.get("LANGUAGE", "")).strip()
    if not lang:
        return True
    return lang.lower() in ("und", "none")


def run_cmd(cmd):
    if DRY_RUN:
        print("    CMD:", " ".join(f'"{x}"' if " " in x else x for x in cmd))
        return True
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ERROR (rc={r.returncode}): {r.stderr.strip()[:300]}")
        return False
    return True


# ── Per-format fixers ─────────────────────────────────────────────────────────


def fix_mkv(path, lang):
    streams = audio_streams(path)
    args = ["mkvpropedit", path]
    for i, s in enumerate(streams):
        if needs_tag(s):
            args += ["--edit", f"track:a{i + 1}", "--set", f"language={lang}"]
    if len(args) > 2:
        if not writable_for_mutate(path):
            return False
        return run_cmd(args)
    return True


def fix_mp4(path, lang):
    # Temp beside the MP4 (same filesystem) — not /tmp: avoids snap-ffmpeg sandbox,
    # sticky /tmp, and leftover langtmp_* owned by another user when mixing sudo and non-sudo.
    dirname = os.path.dirname(path) or "."
    base = os.path.basename(path)
    tmp = os.path.join(dirname, f".langtmp.{base}")
    streams = audio_streams(path)
    meta_args = []
    for i, s in enumerate(streams):
        if needs_tag(s):
            meta_args += [f"-metadata:s:a:{i}", f"language={lang}"]
    if not DRY_RUN:
        _safe_unlink(tmp)
        if not writable_parent_dir(path) or not writable_for_mutate(path):
            return False
    cmd = (
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c", "copy", "-map", "0"]
        + meta_args
        + [tmp]
    )
    ok = run_cmd(cmd)
    if not ok:
        _safe_unlink(tmp)
        return False
    if DRY_RUN:
        return True
    if not os.path.exists(tmp):
        print("    ERROR: temp file not created")
        return False
    try:
        os.replace(tmp, path)
    except OSError as e:
        print(f"    ERROR: could not replace MP4 with patched file: {e}")
        _safe_unlink(tmp)
        return False
    return True


def clean_title(filename):
    """Normalize filename to a bare comparable title (for AVI duplicate detection)."""
    name = os.path.splitext(filename)[0]
    name = re.sub(
        r"[\s\._\-]*(DVDRip|DvDrip|BDRip|BRRip|WEBRip|WEBRIP|HDTV|BluRay|"
        r"WEB[\-\.]?DL|x264|x265|XviD|xvid|DTS|H\.?264|H\.?265|AAC|AC3|"
        r"CD\d+|\d{3,4}p|2160p|4[Kk]|10[Bb]it).*",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"\s*\(\d{4}\)\s*", " ", name)
    name = re.sub(r"\s+\d{4}\s*$", " ", name)
    name = re.sub(r"[\._]+", " ", name)
    name = re.sub(r"\s+-\s+", " ", name)
    name = re.sub(r"\s*-\s*\w+\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def fix_avi(path, lang):
    if not writable_for_mutate(path):
        return "skipped"
    dirname = os.path.dirname(path)
    avi_base = os.path.basename(path)
    avi_clean = clean_title(avi_base)
    for fn in os.listdir(dirname):
        if fn == avi_base:
            continue
        fext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        if fext in ("mp4", "mkv") and clean_title(fn) == avi_clean:
            print(f"    DUPLICATE of '{fn}' → deleting AVI")
            if not DRY_RUN:
                os.remove(path)
            return "deleted"
    out = path.rsplit(".", 1)[0] + ".mkv"
    if os.path.exists(out):
        print(f"    SKIP — MKV already exists: {os.path.basename(out)}")
        return "skipped"
    ok = run_cmd(["mkvmerge", "--default-language", lang, "-o", out, path])
    if ok and not DRY_RUN and os.path.exists(out):
        os.remove(path)
        print(f"    Remuxed → {os.path.basename(out)} (AVI deleted)")
    return "remuxed"


# ── Plex re-analysis ──────────────────────────────────────────────────────────


def plex_analyze_item(file_path):
    try:
        import urllib.parse
        import urllib.request

        token = resolve_plex_token(PLEX_TOKEN_FILE)
        if not token:
            print("  Plex: no token available (set PLEX_TOKEN_FILE or PLEX_TOKEN)")
            return
        url = f"{PLEX_URL}/library/sections/{PLEX_SECTION_ID}/all?type=1&X-Plex-Token={token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
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
                        print(f"  Plex: triggered analyze for ratingKey={key}")
                        return
        print("  Plex: item not found yet (Plex may not have scanned it yet — that's fine)")
    except Exception as e:
        print(f"  Plex: analyze trigger skipped — {e}")


# ── Entry points ──────────────────────────────────────────────────────────────


def run_radarr_mode():
    """Single-file mode: called by Radarr on import/upgrade."""
    event = os.environ.get("radarr_eventtype", "")

    if event == "Test":
        print("[audio_fix_radarr] Test event received — script is working.")
        sys.exit(0)

    if event != "Download":
        print(f"[audio_fix_radarr] Skipping event type: {event!r}")
        sys.exit(0)

    try:
        file_path = validate_media_path(
            os.environ.get("radarr_moviefile_path", ""), MOVIES_DIRS, WEBHOOK_EXTENSIONS
        )
    except ValueError as error:
        print(f"[audio_fix_radarr] {error}", file=sys.stderr)
        sys.exit(1)

    filename = os.path.basename(file_path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    streams = audio_streams(file_path)
    untagged = [s for s in streams if needs_tag(s)]
    if not untagged:
        print(f"[audio_fix_radarr] Already tagged — nothing to do: {filename}")
        sys.exit(0)

    lang = radarr_language()
    if lang:
        print(
            f"[audio_fix_radarr] {filename}  language={lang}  "
            f"(radarr_movie_originallanguage={os.environ.get('radarr_movie_originallanguage')})"
        )
    else:
        lang = DEFAULT_LANG
        title = os.environ.get("radarr_movie_title", filename)
        year = os.environ.get("radarr_movie_year", "")
        msg = (
            f"⚠️ **audio_fix_radarr**: `radarr_movie_originallanguage` not set for "
            f"**{title}** ({year}) — defaulted to `{DEFAULT_LANG}`. "
            f"Check manually if this is a non-English film."
        )
        print(f"[audio_fix_radarr] {filename}  language={DEFAULT_LANG}  (DEFAULT) — {msg}")
        telegram_post(msg)

    ok = False

    if ext == "mkv":
        ok = fix_mkv(file_path, lang)
    elif ext == "mp4":
        ok = fix_mp4(file_path, lang)

    if ok:
        print(f"  Tagged as [{lang}] ✓")
        plex_analyze_item(file_path)
    else:
        sys.exit(1)


def run_bulk_mode():  # noqa: C901
    """Walk MOVIES_DIR and fix all untagged files."""
    mode = "DRY RUN — pass --apply to commit" if DRY_RUN else "*** LIVE MODE ***"
    print(f"=== Audio Language Bulk Fixer ({mode}) ===\n")

    for tool in ["ffprobe", "ffmpeg", "mkvpropedit", "mkvmerge"]:
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            print(f"  MISSING tool: {tool}  →  sudo apt install mkvtoolnix ffmpeg")
            if not DRY_RUN:
                sys.exit(1)

    counts = {"mkv": 0, "mp4": 0, "avi_remuxed": 0, "avi_deleted": 0, "ok": 0}

    dirs_to_scan = [d for d in MOVIES_DIRS if os.path.isdir(d)]
    if not dirs_to_scan:
        print("No movie directories found — check MOVIES_DIRS paths.")
        sys.exit(1)

    for movies_dir in dirs_to_scan:
        print(f"Scanning: {movies_dir}")
    print()

    for movies_dir in dirs_to_scan:
        for root, dirs, files in os.walk(movies_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS]
            for fn in sorted(files):
                if "." not in fn:
                    continue
                ext = fn.rsplit(".", 1)[-1].lower()
                if ext not in BULK_EXTENSIONS:
                    continue
                path = os.path.join(root, fn)
                streams = audio_streams(path)
                untagged = [s for s in streams if needs_tag(s)]
                if not untagged:
                    counts["ok"] += 1
                    continue
                # Bulk mode has no Radarr context → default eng.
                # Library is already correctly tagged; this only catches new stragglers.
                lang = DEFAULT_LANG
                print(f"FIX [{ext.upper()}] [{lang}]  {fn}")
                if ext == "mkv":
                    if fix_mkv(path, lang):
                        counts["mkv"] += 1
                elif ext == "mp4":
                    if fix_mp4(path, lang):
                        counts["mp4"] += 1
                elif ext == "avi":
                    result = fix_avi(path, lang)
                    if result == "deleted":
                        counts["avi_deleted"] += 1
                    elif result == "remuxed":
                        counts["avi_remuxed"] += 1

    print(f"""
=== Summary ===
Already tagged  : {counts["ok"]}
MKV fixed       : {counts["mkv"]}
MP4 fixed       : {counts["mp4"]}
AVI remuxed     : {counts["avi_remuxed"]}
AVI deleted     : {counts["avi_deleted"]}  (duplicate of existing MP4/MKV)
""")
    if DRY_RUN:
        print("→ Dry run complete. Run with --apply to apply.")
    else:
        print("→ Done. Trigger Plex re-analysis if needed:")
        print(
            f'  curl -X PUT "{PLEX_URL}/library/sections/{PLEX_SECTION_ID}/'
            f'analyze?X-Plex-Token=$(cat {PLEX_TOKEN_FILE})"'
        )
        print("  (add section IDs for any additional libraries if needed)")


def main():
    if os.environ.get("radarr_eventtype"):
        run_radarr_mode()
    else:
        run_bulk_mode()


if __name__ == "__main__":
    main()
