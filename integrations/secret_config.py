"""Load credentials from environment variables or mounted secret files."""

import os
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

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

_loaded_secrets: set[str] = set()


class SecretConfigurationError(ValueError):
    """An explicitly configured secret file cannot supply a valid credential."""


def redact_diagnostic(text: str, extra_secrets: Iterable[str | None] = ()) -> str:
    """Redact known credentials and credential-bearing diagnostic URL/header fields."""
    values = _loaded_secrets | {os.getenv(name) for name in SECRET_NAMES} | set(extra_secrets)
    replacements = {
        variant
        for value in values
        if value and value.strip()
        for variant in (value, value.strip(), quote(value, safe=""))
    }
    for value in sorted(replacements, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(/bot)\d+:[^/\s\"'<>]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"([?&](?:x-plex-token|api[_-]?key|token)=)[^&\s\"'<>]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(\b(?:authorization|x-api-key|x-webhook-token|x-plex-token)"
        r"\s*[:=]\s*(?:bearer\s+)?)[^\s,;\"'<>]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
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
        value = os.getenv(name)
        if value and name in SECRET_NAMES:
            _loaded_secrets.add(value)
        return value
    if not file_path.strip():
        raise SecretConfigurationError(
            f"{file_setting} must specify a readable, non-empty UTF-8 secret file"
        )
    try:
        value = Path(file_path).expanduser().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, ValueError):
        raise SecretConfigurationError(
            f"{file_setting} must reference a readable UTF-8 secret file; "
            "check the mount and permissions"
        ) from None
    if not value:
        raise SecretConfigurationError(f"{file_setting} references an empty secret file")
    _loaded_secrets.add(value)
    return value
