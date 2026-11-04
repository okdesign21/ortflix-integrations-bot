"""Payload normalization for webhook inputs."""

from typing import Any

from .models import RadarrWebhook, SonarrWebhook, TautulliWebhook


def _is_placeholder(value) -> bool:
    """Check if value is a templated placeholder or empty string."""
    return (
        isinstance(value, str) and value.startswith("{{") and value.endswith("}}")
    ) or value == ""


def _normalize_request_keys(request: dict) -> dict:
    """Normalize Overseerr request keys to snake_case used by the bot."""
    if not isinstance(request, dict):
        return {}
    mapped = dict(request)
    if "requestedBy_username" in mapped and "requested_by_username" not in mapped:
        mapped["requested_by_username"] = mapped["requestedBy_username"]
    if "requestedBy_email" in mapped and "requested_by_email" not in mapped:
        mapped["requested_by_email"] = mapped["requestedBy_email"]
    if "request_id" not in mapped and "id" in mapped:
        mapped["request_id"] = mapped["id"]
    return mapped


def _normalize_overseerr_payload(raw: dict) -> dict:
    """Normalize Overseerr webhook payload.

    Handles templated keys (e.g., {{media}} -> media) and placeholder values.
    Converts None/empty placeholders to explicit None for numeric fields.
    """
    if not isinstance(raw, dict):
        return {}

    normalized = {}
    for key, value in raw.items():
        # Normalize templated keys: {{media}} -> media
        if isinstance(key, str) and key.startswith("{{") and key.endswith("}}"):
            key = key[2:-2]

        # Process nested dicts and lists
        if isinstance(value, dict):
            nested = {
                k: (None if _is_placeholder(v) and k in {"tmdbId", "tvdbId", "imdbId"} else v)
                for k, v in value.items()
            }
            if key == "request":
                nested = _normalize_request_keys(nested)
            normalized[key] = nested
        elif isinstance(value, list):
            normalized[key] = value
        else:
            normalized[key] = value

    return normalized


def _normalize_radarr_payload(raw: Any) -> dict:
    """Normalize Radarr payload event key casing and nested objects."""
    normalized = RadarrWebhook.model_validate(raw).model_dump(by_alias=True, exclude_unset=True)
    # Keep both keys but guarantee the canonical eventType key exists when possible.
    if "eventType" not in normalized and "event" in normalized:
        normalized["eventType"] = normalized.get("event")
    if "event" not in normalized and "eventType" in normalized:
        normalized["event"] = normalized.get("eventType")

    return normalized


def _normalize_sonarr_payload(raw: Any) -> dict:
    """Validate Sonarr fields while preserving fields unknown to this bot."""
    return SonarrWebhook.model_validate(raw).model_dump(by_alias=True, exclude_unset=True)


def _normalize_tautulli_payload(raw: Any) -> dict:
    """Validate common Tautulli event fields while preserving custom fields."""
    return TautulliWebhook.model_validate(raw).model_dump(by_alias=True, exclude_unset=True)
