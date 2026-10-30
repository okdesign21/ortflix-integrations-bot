#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
configure_arr_webhooks.py — Declare Radarr/Sonarr/Seerr/Tautulli webhooks as code.

Reads config/arr_webhooks.yaml (or --config) and makes the named Webhook connections in
Radarr/Sonarr, the Seerr webhook agent and the named Tautulli Webhook agents match it.
Connections not named in the file are never changed or deleted. If WEBHOOK_TOKEN is set, an
x-webhook-token header is configured; URLs, header values and API keys are never printed.

Dry-run by default; pass --apply to write changes.

  python scripts/configure_arr_webhooks.py [--apply] [--app radarr|sonarr|seerr|tautulli|all]
                                           [--config PATH]
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path

import requests
import yaml
from integration_utils import (
    _env,
    fail,
    overseerr_api_key,
    overseerr_base_url,
    overseerr_session,
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
SEERR_WEBHOOK_PATH = "/api/v1/settings/notifications/webhook"
SEERR_EVENT_BITS = {
    "MEDIA_PENDING": 2,
    "MEDIA_APPROVED": 4,
    "MEDIA_AVAILABLE": 8,
    "MEDIA_FAILED": 16,
    "MEDIA_DECLINED": 64,
    "MEDIA_AUTO_APPROVED": 128,
    "ISSUE_CREATED": 256,
    "ISSUE_COMMENT": 512,
    "ISSUE_RESOLVED": 1024,
    "ISSUE_REOPENED": 2048,
    "MEDIA_AUTO_REQUESTED": 4096,
}
TAUTULLI_WEBHOOK_AGENT_ID = 25
TAUTULLI_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
ALL_APPS = ("radarr", "sonarr", "seerr", "tautulli")

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


def load_desired(path: Path) -> dict:
    """Load and validate the desired webhook definitions."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        fail(f"Missing config file: {path}")
    except (OSError, UnicodeError) as exc:
        fail(f"Cannot read config file {path}: {type(exc).__name__}")
    except yaml.YAMLError as exc:
        fail(f"Invalid YAML in {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"Invalid config shape in {path}: expected top-level object")

    desired: dict = {}
    for app, entries in data.items():
        if app == "seerr":
            validate_seerr(entries)
        elif app == "tautulli":
            validate_tautulli(entries)
        elif app in APPS:
            validate_arr_list(app, entries)
        else:
            fail(f"Unknown app '{app}' in {path}; expected one of {list(ALL_APPS)}")
        desired[app] = entries
    return desired


def validate_arr_list(app: str, entries: object) -> None:
    if not isinstance(entries, list):
        fail(f"'{app}' must be a list of webhook definitions")
    names: set[str] = set()
    for entry in entries:
        validate_entry(app, entry)
        if entry["name"] in names:
            fail(f"Duplicate webhook name '{entry['name']}' for {app}")
        names.add(entry["name"])


def validate_tautulli(entries: object) -> None:
    if not isinstance(entries, list):
        fail("'tautulli' must be a list of webhook definitions")
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail("Each tautulli webhook must be an object")
        require_text("tautulli webhook name", entry.get("name"))
        require_text(f"tautulli webhook '{entry['name']}' url", entry.get("url"))
        if entry["name"] in names:
            fail(f"Duplicate webhook name '{entry['name']}' for tautulli")
        names.add(entry["name"])
        if str(entry.get("method", "POST")).upper() not in TAUTULLI_METHODS:
            fail(f"tautulli webhook '{entry['name']}': method must be one of {TAUTULLI_METHODS}")
        events = entry.get("events")
        if (
            not isinstance(events, list)
            or not events
            or not all(isinstance(e, str) and e.startswith("on_") for e in events)
        ):
            fail(f"tautulli webhook '{entry['name']}': events must be a list of 'on_<event>' names")
        if "json_data_file" in entry:
            require_text("tautulli json_data_file", entry["json_data_file"])
        conditions = entry.get("custom_conditions")
        if conditions is not None and not isinstance(conditions, list):
            fail(f"tautulli webhook '{entry['name']}': custom_conditions must be a list")


def require_text(label: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} must be a non-empty string")


def validate_entry(app: str, entry: object) -> None:
    if not isinstance(entry, dict):
        fail(f"Each {app} webhook must be an object")
    require_text(f"{app} webhook name", entry.get("name"))
    require_text(f"{app} webhook '{entry['name']}' url", entry.get("url"))
    if str(entry.get("method", "POST")).upper() not in METHODS:
        fail(f"{app} webhook '{entry['name']}': method must be one of {sorted(METHODS)}")
    events = entry.get("events")
    if (
        not isinstance(events, list)
        or not events
        or not all(isinstance(e, str) and e.startswith("on") for e in events)
    ):
        fail(f"{app} webhook '{entry['name']}': events must be a list of 'on<Event>' names")


def validate_seerr(entry: object) -> None:
    if not isinstance(entry, dict):
        fail("'seerr' must be an object")
    require_text("seerr url", entry.get("url"))
    events = entry.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(e, str) for e in events):
        fail("seerr events must be a non-empty list of notification type names")
    unknown = sorted(set(events) - set(SEERR_EVENT_BITS))
    if unknown:
        fail(f"seerr: unknown events {unknown}; available: {sorted(SEERR_EVENT_BITS)}")
    for key in ("enabled", "embed_poster"):
        if key in entry and not isinstance(entry[key], bool):
            fail(f"seerr {key} must be true or false")
    if "json_payload_file" in entry:
        require_text("seerr json_payload_file", entry["json_payload_file"])


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
    """Event toggles this connection supports (those whose supports* flag is true)."""
    return {
        key: bool(connection.get(key))
        for key in connection
        if key.startswith("on") and connection.get(f"supports{key[0].upper()}{key[1:]}") is True
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
    if get_field(current, "url") != get_field(wanted, "url"):
        changes.append("url: changed (values not shown)")
    if get_field(current, "method") != get_field(wanted, "method"):
        changes.append(f"method: {get_field(current, 'method')} -> {get_field(wanted, 'method')}")
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
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        fail(f"{method} {url.split('?')[0]} returned non-JSON data")


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
            print(f"  create '{entry['name']}'")
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


def tautulli_call(session: requests.Session, base: str, key: str, cmd: str, **params):
    """Call the Tautulli API; the key travels in the POST body, never in the URL."""
    body = api(session, "POST", f"{base}/api/v2", data={"apikey": key, "cmd": cmd, **params})
    result = (body or {}).get("response", {})
    if result.get("result") != "success":
        fail(f"Tautulli {cmd} failed: {result.get('message') or 'unknown error'}")
    return result.get("data")


def tautulli_wanted(current: dict, entry: dict, token: str | None, config_path: Path) -> dict:
    """Build the set_notifier_config parameters for a declared Tautulli webhook."""
    actions = current.get("actions") or {}
    unknown = sorted(set(entry["events"]) - set(actions))
    if unknown:
        fail(
            f"'{entry['name']}': unsupported tautulli events {unknown}; "
            f"available: {sorted(actions)}"
        )
    wanted = {
        "friendly_name": entry["name"],
        "webhook_hook": entry["url"],
        "webhook_method": str(entry.get("method", "POST")).upper(),
    }
    for action in actions:
        wanted[action] = 1 if action in entry["events"] else 0
    headers = json.dumps({TOKEN_HEADER: token}) if token else ""
    payload = load_json_file(entry, "json_data_file", config_path, "tautulli")
    for action in entry["events"]:
        wanted[f"{action}_subject"] = headers
        if payload is not None:
            wanted[f"{action}_body"] = payload.strip()
    if "custom_conditions" in entry:
        wanted["custom_conditions"] = json.dumps(entry["custom_conditions"])
        wanted["custom_conditions_logic"] = str(entry.get("custom_conditions_logic", ""))
    return wanted


def json_equal(left: str | None, right: str | None) -> bool:
    def parse(text):
        return json.loads(text) if text and text.strip() else None

    try:
        return parse(left) == parse(right)
    except ValueError:
        return False


def tautulli_changes(current: dict, wanted: dict, entry: dict) -> list[str]:
    config = current.get("config") or {}
    notify_text = current.get("notify_text") or {}
    changes = []
    if current.get("friendly_name") != wanted["friendly_name"]:
        changes.append("friendly_name: changed")
    if config.get("hook") != wanted["webhook_hook"]:
        changes.append("url: changed (values not shown)")
    if config.get("method") != wanted["webhook_method"]:
        changes.append(f"method: {config.get('method')} -> {wanted['webhook_method']}")
    for action, enabled in (current.get("actions") or {}).items():
        if int(enabled) != wanted[action]:
            changes.append(f"{action}: {int(enabled)} -> {wanted[action]}")
    for action in entry["events"]:
        text = notify_text.get(action) or {}
        if not json_equal(text.get("subject"), wanted[f"{action}_subject"]):
            changes.append(f"{action} headers: changed (values not shown)")
        if f"{action}_body" in wanted and not json_equal(
            text.get("body"), wanted[f"{action}_body"]
        ):
            changes.append(f"{action} JSON data: changed")
    if (
        "custom_conditions" in wanted
        and current.get("custom_conditions") != entry["custom_conditions"]
    ):
        changes.append("custom_conditions: changed")
    return changes


def sync_tautulli(entries: list[dict], token: str | None, apply: bool, config_path: Path) -> int:
    base = os.getenv("TAUTULLI_URL", "").strip().rstrip("/") or "http://tautulli:8181"
    key = _env("TAUTULLI_API_KEY", required=True)
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    print(f"== tautulli ({base})")

    notifiers = tautulli_call(session, base, key, "get_notifiers")
    pending = 0
    for entry in entries:
        matches = [n for n in notifiers if n.get("friendly_name") == entry["name"]]
        if len(matches) > 1:
            fail(f"tautulli has {len(matches)} notifiers named '{entry['name']}'; resolve manually")
        if matches and int(matches[0].get("agent_id", 0)) != TAUTULLI_WEBHOOK_AGENT_ID:
            fail(f"tautulli notifier '{entry['name']}' exists but is not a Webhook agent")

        if not matches:
            print(f"  create '{entry['name']}'")
            pending += 1
            if apply:
                known = {int(n["id"]) for n in notifiers}
                tautulli_call(
                    session, base, key, "add_notifier_config", agent_id=TAUTULLI_WEBHOOK_AGENT_ID
                )
                notifiers = tautulli_call(session, base, key, "get_notifiers")
                created = [int(n["id"]) for n in notifiers if int(n["id"]) not in known]
                if len(created) != 1:
                    fail(f"Could not identify the new tautulli notifier for '{entry['name']}'")
                current = tautulli_call(
                    session, base, key, "get_notifier_config", notifier_id=created[0]
                )
                wanted = tautulli_wanted(current, entry, token, config_path)
                tautulli_call(
                    session, base, key, "set_notifier_config", notifier_id=created[0], **wanted
                )
            continue

        notifier_id = int(matches[0]["id"])
        current = tautulli_call(session, base, key, "get_notifier_config", notifier_id=notifier_id)
        wanted = tautulli_wanted(current, entry, token, config_path)
        changes = tautulli_changes(current, wanted, entry)
        if not changes:
            print(f"  ok '{entry['name']}'")
            continue
        pending += 1
        print(f"  update '{entry['name']}':")
        for change in changes:
            print(f"    {change}")
        if apply:
            tautulli_call(
                session, base, key, "set_notifier_config", notifier_id=notifier_id, **wanted
            )
    return pending


def encode_payload(text: str) -> str:
    """Seerr stores the payload as base64 of its JSON-string form."""
    return base64.b64encode(json.dumps(text.strip()).encode()).decode()


def decode_payload(value: str | None) -> str | None:
    try:
        return json.loads(base64.b64decode(value or "")).strip()
    except (ValueError, TypeError):
        return None


def load_json_file(entry: dict, field: str, config_path: Path, label: str) -> str | None:
    """Read a JSON template referenced by a config entry, relative to the config file."""
    if field not in entry:
        return None
    path = config_path.parent / entry[field]
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            fail(f"{label} template file {path} is empty")
        # Tautulli placeholders such as {title} sit inside JSON strings, so this still parses.
        json.loads(text)
    except (OSError, UnicodeError) as exc:
        fail(f"Cannot read {label} template file {path}: {type(exc).__name__}")
    except ValueError:
        fail(f"{label} template file {path} is not valid JSON")
    return text


def sync_seerr(entry: dict, token: str | None, apply: bool, config_path: Path) -> int:
    base = overseerr_base_url()
    session = overseerr_session(overseerr_api_key())
    url = f"{base}{SEERR_WEBHOOK_PATH}"
    print(f"== seerr ({base})")

    current = api(session, "GET", url) or {}
    wanted = copy.deepcopy(current)
    options = wanted.setdefault("options", {})
    wanted["enabled"] = entry.get("enabled", True)
    wanted["embedPoster"] = entry.get("embed_poster", True)
    wanted["types"] = sum(SEERR_EVENT_BITS[e] for e in entry["events"])
    options["webhookUrl"] = entry["url"]
    options["customHeaders"] = [{"key": TOKEN_HEADER, "value": token}] if token else []
    payload = load_json_file(entry, "json_payload_file", config_path, "seerr")
    if payload is not None:
        options["jsonPayload"] = encode_payload(payload)

    cur_options = current.get("options", {})
    changes = []
    for key in ("enabled", "embedPoster", "types"):
        if current.get(key) != wanted[key]:
            changes.append(f"{key}: {current.get(key)} -> {wanted[key]}")
    if cur_options.get("webhookUrl") != options["webhookUrl"]:
        changes.append("url: changed (values not shown)")
    if header_map(cur_options.get("customHeaders")) != header_map(options["customHeaders"]):
        have = sorted(header_map(cur_options.get("customHeaders")))
        changes.append(f"headers: {have} -> {sorted(header_map(options['customHeaders']))}")
    if payload is not None and decode_payload(cur_options.get("jsonPayload")) != payload.strip():
        changes.append("jsonPayload: changed")

    if not changes:
        print("  ok 'webhook'")
        return 0
    print("  update 'webhook':")
    for change in changes:
        print(f"    {change}")
    if apply:
        api(session, "POST", url, json=wanted)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--app", choices=[*ALL_APPS, "all"], default="all")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="webhook definitions file")
    args = ap.parse_args()

    desired = load_desired(args.config)
    token = _env("WEBHOOK_TOKEN")
    token = token.strip() if token else None
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    pending = 0
    for app in ALL_APPS if args.app == "all" else (args.app,):
        if not desired.get(app):
            continue
        if app == "seerr":
            pending += sync_seerr(desired[app], token, args.apply, args.config)
        elif app == "tautulli":
            pending += sync_tautulli(desired[app], token, args.apply, args.config)
        else:
            pending += sync_app(app, desired[app], token, args.apply)
    print(f"{'Applied' if args.apply else 'Pending'} changes: {pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
