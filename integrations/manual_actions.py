"""Manual Telegram-triggered automation actions."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

MANUAL_ACTION_TIMEOUT = int(os.getenv("MANUAL_ACTION_TIMEOUT", "600"))
MAX_OUTPUT_CHARS = int(os.getenv("MANUAL_ACTION_OUTPUT_LIMIT", "3500"))

ACTIONS: dict[str, dict[str, object]] = {
    "plex_check_summary": {
        "label": "Plex assets check (summary)",
        "script": "check_plex_titles.py",
        "args": ["--summary"],
        "description": "Compare Plex library items against Kometa asset folders.",
    },
    "user_map_show": {
        "label": "User map (show)",
        "script": "map_users.py",
        "args": ["show"],
        "description": "Show the Plex username to tag name map.",
    },
    "user_map_sync_dry": {
        "label": "User map sync (dry run)",
        "script": "map_users.py",
        "args": ["sync"],
        "description": "Preview user map additions/changes from Tautulli and Overseerr.",
    },
    "user_map_sync_apply": {
        "label": "User map sync (apply)",
        "script": "map_users.py",
        "args": ["sync", "--apply"],
        "description": "Save user map changes (manually locked entries are kept).",
    },
    "audiofix_dry": {
        "label": "Audio lang fix (dry run)",
        "script": "audio_fix_radarr.py",
        "args": [],
        "description": "Preview missing/und audio-language fixes.",
    },
    "audiofix_apply": {
        "label": "Audio lang fix (apply)",
        "script": "audio_fix_radarr.py",
        "args": ["--apply"],
        "description": "Apply missing/und audio-language fixes in place.",
    },
}


def build_manual_actions_markup() -> InlineKeyboardMarkup:
    """Build inline keyboard for manual actions."""
    rows = []
    for action_id, cfg in ACTIONS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    str(cfg["label"]),
                    callback_data=f"manual_run_{action_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def manual_actions_help_text() -> str:
    """Return markdown help text for available manual actions."""
    lines = ["*Manual Actions*", "Choose an action to run in the integrations container:"]
    for cfg in ACTIONS.values():
        lines.append(f"- {cfg['label']}: {cfg['description']}")
    lines.append("")
    lines.append("Use with care: apply actions can modify media files.")
    return "\n".join(lines)


def _truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = max(0, limit - 160)
    omitted = len(text) - head
    return f"{text[:head]}\n\n... [truncated {omitted} chars]"


async def run_manual_action(action_id: str) -> tuple[bool, str]:
    """Execute a manual action and return (ok, markdown_message)."""
    action = ACTIONS.get(action_id)
    if not action:
        return False, f"Unknown action: `{action_id}`"

    script_name = str(action["script"])
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        return False, f"Script not found: `{script_name}`"

    args = [str(a) for a in action.get("args", [])]
    cmd = [sys.executable, str(script_path), *args]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=MANUAL_ACTION_TIMEOUT
        )
    except asyncio.TimeoutError:
        return False, (
            f"`{action['label']}` timed out after {MANUAL_ACTION_TIMEOUT}s. "
            "Increase `MANUAL_ACTION_TIMEOUT` if needed."
        )
    except Exception as err:
        return False, f"Failed to run `{action['label']}`: `{err}`"

    stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()

    parts = [f"*Action:* {action['label']}", f"*Exit code:* `{proc.returncode}`"]
    if stdout:
        parts.append("*stdout:*\n```text\n" + _truncate_output(stdout) + "\n```")
    if stderr:
        parts.append("*stderr:*\n```text\n" + _truncate_output(stderr) + "\n```")

    ok = proc.returncode == 0
    if not ok and not stderr and not stdout:
        parts.append("No output captured.")

    return ok, "\n\n".join(parts)
