#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
map_users.py — View and manage the Plex-username -> tag-name map (see user_map.py).

  python scripts/map_users.py show                  # print the current map
  python scripts/map_users.py sync [--apply]        # add/update automatic entries from
                                                    # Tautulli + Overseerr (locked ones kept)
  python scripts/map_users.py set PLEX TAG [--apply]   # set a manual (locked) entry
  python scripts/map_users.py nickname PLEX [NAME] [--apply]  # set/clear a nickname (unused)
  python scripts/map_users.py remove PLEX [--apply]    # delete an entry

Every command that changes the map is a dry run unless --apply is given.
Changing a mapping affects future tags only; existing Radarr/Sonarr tags are not renamed.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext

import requests
from user_map import (
    fetch_overseerr_users,
    fetch_tautulli_users,
    load_user_map,
    map_lock,
    map_path,
    propose_tag,
    save_user_map,
    user_key,
)


def format_map(entries: dict[str, dict]) -> str:
    if not entries:
        return "(map is empty)"
    width = max(len(k) for k in entries)
    lines = [
        f"{key:<{width}}  ->  {e['tag']:<24} {'manual (locked)' if e['manual'] else 'auto':<16}"
        f"{e.get('nickname', '')}".rstrip()
        for key, e in sorted(entries.items())
    ]
    return "\n".join(lines)


def plan_sync(entries: dict[str, dict], tautulli_users: list[dict], overseerr_users: list[dict]):
    """Return (new_entries, changes); locked entries are never modified."""
    result = {k: dict(v) for k, v in entries.items()}
    changes: list[str] = []
    for t_user in tautulli_users:
        key = user_key(t_user.get("username"))
        if not key:
            continue
        current = result.get(key)
        if current and current["manual"]:
            continue
        tag = propose_tag(t_user["username"], t_user.get("email"), overseerr_users)
        if not current:
            result[key] = {"tag": tag, "manual": False}
            changes.append(f"+ {key} -> {tag}")
        elif current["tag"] != tag:
            changes.append(f"~ {key}: {current['tag']} -> {tag}")
            result[key] = {**current, "tag": tag}
    return result, changes


def cmd_show(_args) -> int:
    print(f"# {map_path()}")
    print(format_map(load_user_map()))
    return 0


def _finish(entries: dict[str, dict], changes: list[str], apply: bool) -> int:
    for line in changes:
        print(f"  {line}")
    if not changes:
        print("No changes.")
    elif apply:
        print(f"Wrote {save_user_map(entries)}")
    else:
        print("Dry run: nothing written. Re-run with --apply to save.")
    return 0


def cmd_sync(args) -> int:
    try:
        tautulli, overseerr = fetch_tautulli_users(), fetch_overseerr_users()
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    entries, changes = plan_sync(load_user_map(), tautulli, overseerr)
    locked = sum(1 for e in entries.values() if e["manual"])
    print(f"{len(entries)} users in map ({locked} locked)")
    return _finish(entries, changes, args.apply)


def cmd_set(args) -> int:
    key, tag = user_key(args.plex), user_key(args.tag)
    if not key or not tag:
        print("Both names need at least one ASCII letter or digit.", file=sys.stderr)
        return 1
    entries = load_user_map()
    old = entries.get(key)
    entries[key] = {**(old or {}), "tag": tag, "manual": True}
    verb = f"{old['tag']} -> {tag}" if old else f"-> {tag}"
    return _finish(entries, [f"= {key} {verb} (manual, locked)"], args.apply)


def cmd_nickname(args) -> int:
    key = user_key(args.plex)
    entries = load_user_map()
    if key not in entries:
        print(f"{args.plex!r} is not in the map; run sync or set first.", file=sys.stderr)
        return 1
    old = entries[key].get("nickname")
    if args.name:
        entries[key]["nickname"] = args.name.strip()
    else:
        entries[key].pop("nickname", None)
    return _finish(entries, [f"= {key} nickname {old!r} -> {args.name or None!r}"], args.apply)


def cmd_remove(args) -> int:
    key = user_key(args.plex)
    entries = load_user_map()
    if key not in entries:
        print(f"{args.plex!r} is not in the map.", file=sys.stderr)
        return 1
    old = entries.pop(key)
    return _finish(entries, [f"- {key} (was {old['tag']})"], args.apply)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the current map").set_defaults(func=cmd_show)
    for name, func, help_text in (
        ("sync", cmd_sync, "add/update automatic entries"),
        ("set", cmd_set, "set a manual (locked) entry"),
        ("nickname", cmd_nickname, "set or clear a nickname (not used yet)"),
        ("remove", cmd_remove, "delete an entry"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name in ("set", "remove", "nickname"):
            p.add_argument("plex", help="Plex username")
        if name == "nickname":
            p.add_argument("name", nargs="?", help="nickname; omit to clear")
        if name == "set":
            p.add_argument("tag", help="tag name (without -watched/-requested)")
        p.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
        p.set_defaults(func=func)
    args = ap.parse_args()
    # Hold the lock across load, modify and save so webhook updates are not overwritten.
    with map_lock() if getattr(args, "apply", False) else nullcontext():
        return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
