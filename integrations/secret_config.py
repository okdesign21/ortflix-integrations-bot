"""Load credentials from environment variables or mounted secret files."""

import os
from pathlib import Path

SECRET_NAMES = frozenset(
    {
        "TELEGRAM_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "OVERSEERR_API_KEY",
        "API_KEY",
        "SEERR_API_KEY",
        "RADARR_API_KEY",
        "SONARR_API_KEY",
        "WEBHOOK_TOKEN",
        "PLEX_TOKEN",
        "DISCORD_WEBHOOK_URL",
    }
)


def validate_secret_files() -> None:
    """Reject invalid explicit files, including credentials for optional actions."""
    for name in sorted(SECRET_NAMES):
        if f"{name}_FILE" in os.environ:
            load_secret(name)


def load_secret(name: str) -> str | None:
    """Prefer an explicitly configured file; never fall back after a file error."""
    file_setting = f"{name}_FILE"
    file_path = os.getenv(file_setting)
    if file_path is None:
        return os.getenv(name)
    if not file_path.strip():
        raise ValueError(f"{file_setting} must specify a readable, non-empty UTF-8 secret file")
    try:
        value = Path(file_path).expanduser().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, ValueError):
        raise ValueError(
            f"{file_setting} must reference a readable UTF-8 secret file; "
            "check the mount and permissions"
        ) from None
    if not value:
        raise ValueError(f"{file_setting} references an empty secret file")
    return value
