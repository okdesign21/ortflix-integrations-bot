#!/usr/bin/env python3
"""Compare Plex library items to Kometa-style asset folder names on disk.

Credentials (first match wins for URL / token):

- Shared bot environment (`PLEX_URL`, `PLEX_TOKEN_FILE`, `PLEX_TOKEN`).
- Optional .env next to this script; only fills unset vars.
- Values may be literals, a file path, or `sudo cat /path/to/secret` on this machine.

Set `shared.plex.url` in the config file or `PLEX_URL` in the environment when
the bot cannot reach Plex at the default address.

Usage:
    python3 scripts/check_plex_titles.py
    python3 scripts/check_plex_titles.py Films "4K Movies"
    python3 scripts/check_plex_titles.py Films Series --summary
    python3 scripts/check_plex_titles.py Films --asset-dir /path/to/Movies_Shows
"""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from integration_utils import load_bot_settings, resolve_plex_token, resolve_plex_url
from plexapi.server import PlexServer

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DOTENV_PATH = PROJECT_ROOT / ".env"
BOT_SETTINGS = load_bot_settings()
PLEX_TITLE_SETTINGS = BOT_SETTINGS["plex_title_check"]
DEFAULT_ASSET_DIR = Path(os.getenv("KOMETA_ASSET_DIR", PLEX_TITLE_SETTINGS["default_asset_dir"]))
EXCEPTION_MAPPINGS_PATH = Path(
    os.getenv(
        "KOMETA_EXCEPTION_MAPPINGS",
        PLEX_TITLE_SETTINGS["exception_mappings"],
    )
)

DEFAULT_LIBRARIES = tuple(PLEX_TITLE_SETTINGS["default_libraries"])

GUID_PREFIXES = tuple(PLEX_TITLE_SETTINGS["guid_prefixes"].items())


def _load_simple_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_plex_config() -> tuple[str, str]:
    shared_plex = BOT_SETTINGS["shared"]["plex"]
    url = resolve_plex_url(str(shared_plex["url"]))
    token = resolve_plex_token(str(shared_plex["token_file"]))

    if not url or not token:
        lines = []
        if not url:
            lines.append(
                "Plex URL: set PLEX_URL or override shared.plex.url in the config file."
            )
        if not token:
            lines.append(
                "Plex token: set PLEX_TOKEN_FILE or PLEX_TOKEN."
            )
        print("Error:\n" + "\n".join(f"  {x}" for x in lines))
        raise SystemExit(1)

    return url, token


def _load_exception_mappings(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize_name(name: str, exception_mappings: dict[str, str]) -> str:
    if name in exception_mappings:
        return exception_mappings[name]
    name = name.replace(":", " -")
    if name in exception_mappings:
        return exception_mappings[name]
    return name


def _extract_ids(item: Any) -> set[str]:
    ids: set[str] = set()
    for guid in getattr(item, "guids", None) or []:
        gid = str(getattr(guid, "id", ""))
        for prefix, label in GUID_PREFIXES:
            if gid.startswith(prefix):
                clean = gid[len(prefix) :].split("?")[0]
                ids.add(f"{label}{clean}")
    return ids


def _folder_asset_name(item: Any) -> str:
    try:
        media = getattr(item, "media", None)
        if media:
            parts = media[0].parts if hasattr(media[0], "parts") else []
            if parts:
                return Path(parts[0].file).parent.name
    except (AttributeError, IndexError, TypeError):
        pass
    y = getattr(item, "year", None)
    title = getattr(item, "title", "") or ""
    return f"{title} ({y})" if y else title


def _title_variations_for_orphan(item: Any) -> set[str]:
    t = getattr(item, "title", "") or ""
    out = {t}
    y = getattr(item, "year", None)
    if y is not None:
        out.add(f"{t} ({y})")
    return out


def _folder_has_poster(asset_root: Path, folder_name: str) -> bool:
    folder = asset_root / folder_name
    if not folder.is_dir():
        return False
    return any(p.is_file() for p in folder.glob("poster.*"))


def analyze_section(
    section: Any,
    asset_root: Path,
    asset_folders: set[str],
    exception_mappings: dict[str, str],
) -> tuple[
    list[tuple[str, Any, str]],
    list[tuple[str, Any, str]],
    list[tuple[str, Any, str]],
    list[Any],
    set[str],
]:
    """Return (matched_rows, matched_no_poster_rows, missing_rows, items, claimed_folders)."""
    items = section.all()
    matched: list[tuple[str, Any, str]] = []
    matched_no_poster: list[tuple[str, Any, str]] = []
    missing: list[tuple[str, Any, str]] = []
    claimed: set[str] = set()

    def sort_key(it: Any) -> str:
        return (getattr(it, "title", "") or "").lower()

    for item in sorted(items, key=sort_key):
        title = getattr(item, "title", "") or ""
        year = getattr(item, "year", None)
        year_disp = year if year is not None else "N/A"
        folder_name = _folder_asset_name(item)
        candidate_ids = _extract_ids(item)

        variations = [
            title,
            f"{title} ({year_disp})",
            _normalize_name(title, exception_mappings),
            _normalize_name(f"{title} ({year_disp})", exception_mappings),
            folder_name,
        ]
        for ext_id in candidate_ids:
            variations.append(f"{title} ({year_disp}) {{{ext_id}}}")
            variations.append(f"{title} {{{ext_id}}}")

        found = next((v for v in variations if v in asset_folders), None)
        if found:
            if _folder_has_poster(asset_root, found):
                note = f'matched as "{found}"' if found != title else "matched"
            else:
                note = (
                    f'matched as "{found}" but missing poster.* file'
                    if found != title
                    else "matched but missing poster.* file"
                )
                matched_no_poster.append((title, year_disp, found))
            matched.append((title, year_disp, note))
            claimed.add(found)
        else:
            missing.append((title, year_disp, folder_name))

    return matched, matched_no_poster, missing, items, claimed


def _print_library_report(
    section_title: str,
    matched: list[tuple[str, Any, str]],
    matched_no_poster: list[tuple[str, Any, str]],
    missing: list[tuple[str, Any, str]],
    *,
    summary_only: bool,
) -> None:
    sep = "-" * 80
    print(sep)
    print(f"Library: {section_title}")
    print(sep)
    print(f"With asset folder match: {len(matched)}")
    if not summary_only:
        for title, year, note in matched:
            print(f"  [OK]   {title} ({year})  {note}")
    print()
    print(f"Matched but poster missing: {len(matched_no_poster)}")
    if not summary_only:
        for title, year, folder in matched_no_poster:
            print(f"  [NOP]  {title} ({year})  folder has no poster.* file: {folder}")
    print()
    print(f"Missing asset folder: {len(missing)}")
    if not summary_only:
        for title, year, expected in missing:
            print(f"  [MISS] {title} ({year})  expected folder name: {expected}")
    print()


def main() -> None:
    _load_simple_dotenv(DOTENV_PATH)

    parser = ArgumentParser(
        description="List Plex library items and compare folder names to asset directories."
    )
    parser.add_argument(
        "libraries",
        nargs="*",
        metavar="LIBRARY",
        help="Plex library names (default: Films). Example: Films Series",
    )
    parser.add_argument(
        "--asset-dir",
        default=None,
        help=f"Asset root (default: {DEFAULT_ASSET_DIR})",
    )
    parser.add_argument(
        "--summary",
        "-s",
        action="store_true",
        help="Counts per library and final summary only (no per-item [OK]/[MISS] lines).",
    )
    args = parser.parse_args()

    library_names = list(args.libraries) if args.libraries else list(DEFAULT_LIBRARIES)

    plex_url, plex_token = load_plex_config()
    print(f"Plex: {plex_url}")
    print(f"Libraries: {', '.join(library_names)}")
    print()

    plex = PlexServer(plex_url, plex_token)
    available = {s.title: s for s in plex.library.sections()}
    missing_names = [n for n in library_names if n not in available]
    if missing_names:
        print("Error: unknown library name(s):", ", ".join(missing_names))
        print("Available:", ", ".join(sorted(available.keys())))
        raise SystemExit(1)

    asset_dir = Path(args.asset_dir) if args.asset_dir else DEFAULT_ASSET_DIR
    if asset_dir.is_dir():
        asset_folders = {
            name
            for name in os.listdir(asset_dir)
            if (asset_dir / name).is_dir() and not name.startswith(".")
        }
    else:
        print(f"Warning: asset directory does not exist or is not a directory: {asset_dir}")
        print("Treating as no asset folders.")
        asset_folders = set()

    exception_mappings = _load_exception_mappings(EXCEPTION_MAPPINGS_PATH)
    if not EXCEPTION_MAPPINGS_PATH.is_file() and EXCEPTION_MAPPINGS_PATH.parent.is_dir():
        print(f"Note: optional file not found: {EXCEPTION_MAPPINGS_PATH}")
        print()

    all_items: list[Any] = []
    all_title_hints: set[str] = set()

    grand_matched = 0
    grand_matched_no_poster = 0
    grand_missing = 0

    all_claimed_folders: set[str] = set()

    for name in library_names:
        section = available[name]
        matched, matched_no_poster, missing, items, claimed = analyze_section(
            section, asset_dir, asset_folders, exception_mappings
        )
        all_items.extend(items)
        for it in items:
            all_title_hints |= _title_variations_for_orphan(it)
        all_claimed_folders |= claimed
        grand_matched += len(matched)
        grand_matched_no_poster += len(matched_no_poster)
        grand_missing += len(missing)
        _print_library_report(
            name,
            matched,
            matched_no_poster,
            missing,
            summary_only=args.summary,
        )

    orphaned = (asset_folders - all_title_hints) - all_claimed_folders
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Libraries checked:     {len(library_names)}")
    print(f"  Total items scanned:   {len(all_items)}")
    print(f"  With asset match:      {grand_matched}")
    print(f"  Match but no poster:   {grand_matched_no_poster}")
    print(f"  Missing asset folder:  {grand_missing}")
    print(
        f"  Orphan asset folders:  {len(orphaned)}  "
        "(folder name not equal to item title or 'title (year)' in scanned libraries)"
    )
    if orphaned:
        print()
        print("Orphan folders (by name):")
        for folder in sorted(orphaned):
            print(f"  {folder}")
    print()


if __name__ == "__main__":
    main()
