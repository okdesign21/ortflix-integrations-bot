"""Centralized configuration for Telegram bot integrations.

Manages URLs, API keys, and extensible webhook handlers.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import yaml

from integrations.secret_config import load_secret, validate_secret_files


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean env var from common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ====== UTILITIES ======
def _clean_username(name: Optional[str]) -> Optional[str]:
    """Slugify username: ASCII alphanumeric + hyphens only (for display).
    Returns None if username contains non-ASCII characters.
    """
    if not name:
        return None
    # Only keep ASCII letters, digits, and replace others with hyphens
    cleaned = "".join(ch.lower() if (ch.isascii() and ch.isalnum()) else "-" for ch in str(name))
    cleaned = cleaned.strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    # If no valid ASCII alphanumeric chars remain, return None.
    if not cleaned or not any(ch.isascii() and ch.isalnum() for ch in cleaned):
        return None
    return cleaned


# ====== SERVICE URLS ======
DEFAULT_BOT_SETTINGS_PATH = Path(__file__).resolve().parent / "config" / "bot_settings.yaml"
BOT_SETTINGS_OVERRIDE_CANDIDATES = (
    Path("/config/bot_settings.yaml"),
    Path("/config/bot_settings.yml"),
)
BOT_SETTINGS_ENV_VAR = "ORTFLIX_BOT_SETTINGS_FILE"


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def _load_settings_file(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config shape in {path}: expected top-level object")
    return data


@lru_cache(maxsize=1)
def _load_bot_settings() -> dict:
    settings = _load_settings_file(DEFAULT_BOT_SETTINGS_PATH)
    override_raw = os.getenv(BOT_SETTINGS_ENV_VAR, "").strip()
    override_path = Path(override_raw).expanduser() if override_raw else None
    if override_path:
        if not override_path.is_file():
            raise ValueError(f"{BOT_SETTINGS_ENV_VAR} points to a missing file: {override_path}")
        return _deep_merge_dicts(settings, _load_settings_file(override_path))

    for candidate in BOT_SETTINGS_OVERRIDE_CANDIDATES:
        if candidate.is_file():
            return _deep_merge_dicts(settings, _load_settings_file(candidate))
    return settings


def _service_default_url(service_name: str, fallback: str) -> str:
    shared = _load_bot_settings().get("shared", {})
    services = shared.get("services", {})
    service_cfg = services.get(service_name, {})
    url = service_cfg.get("url")
    if isinstance(url, str) and url.strip():
        return url.rstrip("/")
    return fallback.rstrip("/")


def _get_service_url(
    env_var: str,
    service_name: str,
    default_host: str,
    default_port: int,
    legacy_env_var: str | None = None,
) -> str:
    """Get service URL from env override, host/port, or config default.

    Args:
        env_var: Environment variable name for the URL (e.g., "OVERSEERR_URL")
        service_name: Service key under shared.services in bot settings
        default_host: Primary default hostname
        default_port: Default port number
    """
    url = os.getenv(env_var)
    if not url and legacy_env_var:
        url = os.getenv(legacy_env_var)
    if url:
        return url.rstrip("/")
    host = os.getenv(f"{env_var.replace('_URL', '_HOST')}")
    port = os.getenv(f"{env_var.replace('_URL', '_PORT')}")
    if not host and legacy_env_var:
        host = os.getenv(f"{legacy_env_var.replace('_URL', '_HOST')}")
    if not port and legacy_env_var:
        port = os.getenv(f"{legacy_env_var.replace('_URL', '_PORT')}")
    if host or port:
        return f"http://{host or default_host}:{port or default_port}".rstrip("/")
    return _service_default_url(service_name, f"http://{default_host}:{default_port}")


# Service URLs (for both bot and script usage)
OVERSEERR_URL = _get_service_url(
    "OVERSEERR_URL", "overseerr", "overseerr", 5055, legacy_env_var="SEERR_URL"
)
RADARR_URL = _get_service_url("RADARR_URL", "radarr", "radarr", 7878)
SONARR_URL = _get_service_url("SONARR_URL", "sonarr", "sonarr", 8989)

# API Keys
validate_secret_files()
OVERSEERR_API_KEY = load_secret("OVERSEERR_API_KEY") or load_secret("API_KEY")
RADARR_API_KEY = load_secret("RADARR_API_KEY")
SONARR_API_KEY = load_secret("SONARR_API_KEY")

# Telegram
TELEGRAM_TOKEN = load_secret("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID_RAW = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 7777))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")  # nosec B104
WEBHOOK_TOKEN = load_secret("WEBHOOK_TOKEN")
ALLOW_INSECURE_WEBHOOKS = _env_bool("ALLOW_INSECURE_WEBHOOKS", default=False)
TELEGRAM_PORT = WEBHOOK_PORT
TELEGRAM_HOST = WEBHOOK_HOST

# ====== WEBHOOK HANDLERS ======
# Registry: notification_type -> (caption_builder, reply_markup_builder)
# Return tuple of (caption: str, reply_markup: InlineKeyboardMarkup | None)
WEBHOOK_HANDLERS = {}


def register_webhook_handler(
    notification_type: str,
    caption_builder: Callable,
    reply_markup_builder: Optional[Callable] = None,
) -> None:
    """Register a webhook handler for a notification type.

    Args:
        notification_type: e.g., "MEDIA_PENDING"
        caption_builder: Callable(payload: dict) -> str
        reply_markup_builder: Optional callable(payload: dict) -> InlineKeyboardMarkup
    """
    WEBHOOK_HANDLERS[notification_type] = {
        "caption": caption_builder,
        "reply_markup": reply_markup_builder,
    }


# ====== BUILT-IN HANDLERS ======
def _season_candidates(payload: dict) -> list:
    """Extract season candidates from various payload locations."""
    request_info = payload.get("request", {})
    media_info = payload.get("media", {})
    extra_info = payload.get("extra", [])

    # Check request and media for season keys
    for key in ("seasons", "requested_seasons", "requestedSeasons"):
        if key in request_info:
            seasons = request_info.get(key) or []
            if seasons:
                return seasons
        if key in media_info:
            seasons = media_info.get(key) or []
            if seasons:
                return seasons

    # Check extra array for season information (Overseerr format)
    if extra_info and isinstance(extra_info, list):
        return extra_info

    return []


def _season_from_item(item) -> Optional[int]:
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.isdigit():
        return int(item)
    if isinstance(item, dict):
        # Check if it's an 'extra' format: {'name': 'Requested Seasons', 'value': '1'}
        if item.get("name") == "Requested Seasons" and "value" in item:
            value = item["value"]
            # Handle comma-separated seasons like "1,2,3" or single season "1"
            if isinstance(value, str):
                return [int(s.strip()) for s in value.split(",") if s.strip().isdigit()]
            if isinstance(value, int):
                return value

        # Check standard season fields
        for field in ("seasonNumber", "season_number", "season", "number"):
            value = item.get(field)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _extract_season_numbers(payload: dict) -> list[int]:
    season_numbers: list[int] = []
    for item in _season_candidates(payload):
        season = _season_from_item(item)
        if season is not None:
            # Handle both single integers and lists
            if isinstance(season, list):
                season_numbers.extend(season)
            else:
                season_numbers.append(season)

    return sorted(set(season_numbers))


def _format_season_line(payload: dict) -> str:
    seasons = _extract_season_numbers(payload)
    if not seasons:
        return ""
    label = "Season" if len(seasons) == 1 else "Seasons"
    season_list = ", ".join(str(season) for season in seasons)
    return f"\n {label}: {season_list}"


def _format_profile_line(request_info: dict) -> str:
    """Resolved quality profile name only (Seerr webhook request block has no profile fields)."""
    if not isinstance(request_info, dict):
        return ""
    name = request_info.get("profile_name") or request_info.get("profileName")
    if not name or not str(name).strip():
        return ""
    return f"\n *Profile:* {name}"


def _build_media_pending_caption(payload: dict) -> str:
    """Build caption for MEDIA_PENDING notification."""
    media = payload.get("media") or {}
    media_type = media.get("media_type") or media.get("mediaType", "movie")
    subject = payload.get("subject", "Unknown title")
    request_info = payload.get("request", {})
    username = request_info.get("requested_by_username", "")
    email = request_info.get("requested_by_email", "")

    requester = username.strip() if username else ""

    # If username is empty, try email prefix
    if not requester and email and "@" in email:
        requester = email.split("@")[0].strip()

    # Final default to "Someone"
    if not requester:
        requester = "Someone"

    emoji = "📺" if media_type == "tv" else "🎬"
    season_line = _format_season_line(payload) if media_type == "tv" else ""
    profile_line = _format_profile_line(request_info)

    return (
        f"{emoji} *Request Pending Approval* - {subject}"
        f"{season_line}{profile_line}\n Requested by: {requester}"
    )


def _build_media_pending_markup(payload: dict):
    """Build inline buttons for MEDIA_PENDING."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    request_id = payload.get("request", {}).get("request_id")
    if not request_id:
        return None

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{request_id}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"decline_{request_id}"),
            ]
        ]
    )


def _build_media_available_caption(payload: dict) -> str:
    """Build caption for MEDIA_AVAILABLE notification."""
    media = payload.get("media") or {}
    media_type = media.get("media_type") or media.get("mediaType", "movie")
    subject = payload.get("subject", "Unknown title")
    emoji = "📺" if media_type == "tv" else "🎬"
    season_line = _format_season_line(payload) if media_type == "tv" else ""
    request_info = payload.get("request") or {}
    profile_line = _format_profile_line(request_info)

    parts = [f"{emoji} *Request Available!*", f"{subject}{season_line}"]
    if profile_line.strip():
        parts.append(profile_line.strip())

    quality = payload.get("downloaded_quality")
    if quality:
        parts.append(f"*Quality:* {quality}")

    if media_type == "movie":
        folder = payload.get("movie_folder")
        if folder:
            parts.append(f"*Folder:* `{folder}`")

    return "\n".join(parts)


def _build_media_failed_caption(payload: dict) -> str:
    """Build caption for MEDIA_FAILED notification."""
    subject = payload.get("subject", "Unknown title")
    request_info = payload.get("request") or {}
    profile_line = _format_profile_line(request_info)
    return (
        f"⚠️ *Request Failed*\n{subject}{profile_line}\n"
        f"❌ The request could not be processed.\n"
        f"Check Radarr / Sonarr logs."
    )


# Register built-in handlers
register_webhook_handler(
    "MEDIA_PENDING",
    caption_builder=_build_media_pending_caption,
    reply_markup_builder=_build_media_pending_markup,
)
register_webhook_handler(
    "MEDIA_AVAILABLE",
    caption_builder=_build_media_available_caption,
)
register_webhook_handler(
    "MEDIA_FAILED",
    caption_builder=_build_media_failed_caption,
)


# ====== CALLBACK ACTION HANDLERS ======
# Registry: callback_prefix -> handler function
# Callable(action: str, data_parts: list[str], chat_id: int, query)
CALLBACK_HANDLERS = {}


def register_callback_handler(action_prefix: str, handler: Callable) -> None:
    """Register a callback action handler.

    Args:
        action_prefix: e.g., "approve", "redownload"
        handler: Callable(action: str, data_parts: list[str], chat_id: int, query)
    """
    CALLBACK_HANDLERS[action_prefix] = handler


# Built-in callback handlers will be registered in bot.py to avoid circular imports


# ====== VALIDATION ======
def validate_config() -> None:
    """Validate required config and raise ValueError if missing."""
    for var_name, var_value in [
        ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
        ("OVERSEERR_API_KEY", OVERSEERR_API_KEY),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID_RAW),
    ]:
        if not var_value:
            raise ValueError(f"{var_name} is not set")

    try:
        int(TELEGRAM_CHAT_ID_RAW)
    except (TypeError, ValueError) as err:
        raise ValueError("TELEGRAM_CHAT_ID must be an integer") from err

    if WEBHOOK_TOKEN and not WEBHOOK_TOKEN.strip():
        raise ValueError("WEBHOOK_TOKEN must not contain only whitespace")

    if not WEBHOOK_TOKEN and not ALLOW_INSECURE_WEBHOOKS:
        raise ValueError(
            "WEBHOOK_TOKEN is not set. Set WEBHOOK_TOKEN or explicitly allow insecure mode via "
            "ALLOW_INSECURE_WEBHOOKS=true only on trusted private networks."
        )


def get_telegram_chat_id() -> int:
    """Parse and return TELEGRAM_CHAT_ID as integer."""
    return int(TELEGRAM_CHAT_ID_RAW)
