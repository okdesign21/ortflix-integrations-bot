#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
configure_arr_webhooks.py — Declare Radarr/Sonarr webhook connections as code.

Reads config/arr_webhooks.yaml (or --config) and makes the named Webhook connections
in Radarr/Sonarr match it. Connections not named in the file are never changed or
deleted. If WEBHOOK_TOKEN is set, an x-webhook-token header is configured; header
values are never printed.

Dry-run by default; pass --apply to write changes.

  python scripts/configure_arr_webhooks.py [--apply] [--app radarr|sonarr|both] [--config PATH]
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import requests
import yaml
from integration_utils import (
    _env,
    fail,
    radarr_base_url,
    radarr_session,
    sonarr_base_url,
    sonarr_session,
)

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "arr_webhooks.yaml"
WEBHOOK_IMPLEMENTATION = "Webhook"
TOKEN_HEADER = "x-webhook-token"
METHODS = {"POST": 1, "PUT": 2}
REQUEST_TIMEOUT = 30

APPS = {
    "radarr": {
        "base_url": radarr_base_url,
        "session": radarr_session,
        "api_key_env": "RADARR_API_KEY",
    },
    "sonarr": {
        "base_url": sonarr_base_url,
        "session": sonarr_session,
        "api_key_env": "SONARR_API_KEY",
    },
}


def load_desired(path: Path) -> dict[str, list[dict]]:
    """Load and validate the desired webhook definitions."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        fail(f"Missing config file: {path}")
    except yaml.YAMLError as exc:
        fail(f"Invalid YAML in {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"Invalid config shape in {path}: expected top-level object")

    desired: dict[str, list[dict]] = {}
    for app, entries in data.items():
        if app not in APPS:
            fail(f"Unknown app '{app}' in {path}; expected one of {sorted(APPS)}")
        if not isinstance(entries, list):
            fail(f"'{app}' must be a list of webhook definitions")
        names: set[str] = set()
        for entry in entries:
            validate_entry(app, entry)
            if entry["name"] in names:
                fail(f"Duplicate webhook name '{entry['name']}' for {app}")
            names.add(entry["name"])
        desired[app] = entries
    return desired


def validate_entry(app: str, entry: object) -> None:
    if not isinstance(entry, dict):
        fail(f"Each {app} webhook must be an object")
    for key in ("name", "url", "events"):
        if not entry.get(key):
            fail(f"{app} webhook is missing '{key}'")
    if str(entry.get("method", "POST")).upper() not in METHODS:
        fail(f"{app} webhook '{entry['name']}': method must be one of {sorted(METHODS)}")
    events = entry["events"]
    if not isinstance(events, list) or not all(
        isinstance(e, str) and e.startswith("on") for e in events
    ):
        fail(f"{app} webhook '{entry['name']}': events must be a list of 'on<Event>' names")


def get_field(connection: dict, name: str):
    for field in connection.get("fields", []):
        if field.get("name") == name:
            return field.get("value")
    return None


def set_field(connection: dict, name: str, value) -> None:
    for field in connection.get("fields", []):
        if field.get("name") == name:
            field["value"] = value
            return
    connection.setdefault("fields", []).append({"name": name, "value": value})


def header_map(headers) -> dict[str, str]:
    return {str(h["key"]).lower(): str(h.get("value", "")) for h in headers or [] if h.get("key")}


def event_flags(connection: dict) -> dict[str, bool]:
    """Event toggles the API exposes for this connection (those with a supports* flag)."""
    return {
        key: bool(connection.get(key))
        for key in connection
        if key.startswith("on") and f"supports{key[0].upper()}{key[1:]}" in connection
    }


def build_desired(base: dict, entry: dict, token: str | None) -> dict:
    """Apply a declared definition onto a connection (existing or schema template)."""
    connection = copy.deepcopy(base)
    available = event_flags(connection)
    unknown = sorted(set(entry["events"]) - set(available))
    if unknown:
        fail(f"'{entry['name']}': unsupported events {unknown}; available: {sorted(available)}")

    connection["name"] = entry["name"]
    for event in available:
        connection[event] = event in entry["events"]
    set_field(connection, "url", entry["url"])
    set_field(connection, "method", METHODS[str(entry.get("method", "POST")).upper()])
    headers = [{"key": TOKEN_HEADER, "value": token}] if token else []
    set_field(connection, "headers", headers)
    return connection


def describe_changes(current: dict, wanted: dict) -> list[str]:
    changes = []
    for event, enabled in sorted(event_flags(wanted).items()):
        if event_flags(current).get(event) != enabled:
            changes.append(f"{event}: {event_flags(current).get(event)} -> {enabled}")
    for field in ("url", "method"):
        if get_field(current, field) != get_field(wanted, field):
            changes.append(f"{field}: {get_field(current, field)} -> {get_field(wanted, field)}")
    if header_map(get_field(current, "headers")) != header_map(get_field(wanted, "headers")):
        have = sorted(header_map(get_field(current, "headers")))
        want = sorted(header_map(get_field(wanted, "headers")))
        changes.append(f"headers: {have} -> {want} (values not shown)")
    return changes


def api(session: requests.Session, method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        response = session.request(method, url, **kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        fail(f"{method} {url.split('?')[0]} failed ({detail})")
    return response.json() if response.content else None


def fetch_schema_template(session: requests.Session, base: str) -> dict:
    for item in api(session, "GET", f"{base}/api/v3/notification/schema"):
        if item.get("implementation") == WEBHOOK_IMPLEMENTATION:
            return item
    fail("Webhook schema not found in the API response")


def sync_app(app: str, entries: list[dict], token: str | None, apply: bool) -> int:
    cfg = APPS[app]
    base = cfg["base_url"]()
    session = cfg["session"](_env(cfg["api_key_env"], required=True))
    print(f"== {app} ({base})")

    existing = api(session, "GET", f"{base}/api/v3/notification")
    by_name: dict[str, list[dict]] = {}
    for connection in existing:
        by_name.setdefault(connection.get("name"), []).append(connection)

    pending = 0
    for entry in entries:
        matches = by_name.get(entry["name"], [])
        if len(matches) > 1:
            fail(f"{app} has {len(matches)} connections named '{entry['name']}'; resolve manually")
        if matches and matches[0].get("implementation") != WEBHOOK_IMPLEMENTATION:
            fail(f"{app} connection '{entry['name']}' exists but is not a Webhook")

        if not matches:
            wanted = build_desired(fetch_schema_template(session, base), entry, token)
            print(f"  create '{entry['name']}' -> {entry['url']}")
            pending += 1
            if apply:
                api(session, "POST", f"{base}/api/v3/notification", json=wanted)
            continue

        current = matches[0]
        wanted = build_desired(current, entry, token)
        changes = describe_changes(current, wanted)
        if not changes:
            print(f"  ok '{entry['name']}'")
            continue
        pending += 1
        print(f"  update '{entry['name']}':")
        for change in changes:
            print(f"    {change}")
        if apply:
            api(session, "PUT", f"{base}/api/v3/notification/{current['id']}", json=wanted)
    return pending


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--app", choices=["radarr", "sonarr", "both"], default="both")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="webhook definitions file")
    args = ap.parse_args()

    desired = load_desired(args.config)
    token = _env("WEBHOOK_TOKEN")
    token = token.strip() if token else None
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    pending = 0
    for app in ("radarr", "sonarr") if args.app == "both" else (args.app,):
        pending += sync_app(app, desired.get(app, []), token, args.apply)
    print(f"{'Applied' if args.apply else 'Pending'} changes: {pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
