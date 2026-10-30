"""Overseerr webhook integration and API helpers."""

import json
import logging
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app_config import OVERSEERR_API_KEY, OVERSEERR_URL, RADARR_API_KEY, RADARR_URL, WEBHOOK_TOKEN

from .models import OverseerrWebhook
from .payloads import _normalize_overseerr_payload, _normalize_request_keys
from .webhook_auth import require_valid_webhook_token

logger = logging.getLogger(__name__)

router = APIRouter()

_REQUEST_STATUS_APPROVED = 2
_REQUEST_STATUS_DECLINED = 3

_sender: Optional[Callable[[Optional[str], str, Any], Awaitable[None]]] = None


def _default_webhook_token() -> Optional[str]:
    return WEBHOOK_TOKEN


_webhook_token_getter: Callable[[], Optional[str]] = _default_webhook_token


def configure_sender(sender: Callable[[Optional[str], str, Any], Awaitable[None]]) -> None:
    """Set message sender callback used by webhook routes."""
    global _sender
    _sender = sender


def configure_webhook_token_getter(getter: Callable[[], Optional[str]]) -> None:
    """Set webhook token accessor for runtime/test compatibility."""
    global _webhook_token_getter
    _webhook_token_getter = getter


async def call_overseerr_api(endpoint: str, method: str = "POST", json_data: dict = None) -> dict:
    """Make an API call to Overseerr."""
    session = aiohttp.ClientSession()
    try:
        method_upper = method.upper()
        request_func = {
            "GET": session.get,
            "POST": session.post,
            "PUT": session.put,
            "DELETE": session.delete,
        }.get(method_upper, session.request)

        headers = {"X-Api-Key": OVERSEERR_API_KEY}
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        kwargs: dict = {"headers": headers}
        if json_data is not None:
            kwargs["json"] = json_data

        url = f"{OVERSEERR_URL}{endpoint}"
        async with request_func(url, **kwargs) as response:
            if response.status >= 400:
                raise RuntimeError(f"Overseerr API error {response.status}")
            body = await response.text()
            logger.info("Seerr API %s %s -> HTTP %s", method_upper, endpoint, response.status)
            if not body:
                return {}
            return json.loads(body)
    finally:
        close_result = session.close()
        if hasattr(close_result, "__await__"):
            await close_result


def _request_status_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def call_overseerr(request_id: str, action: str) -> None:
    """Approve or decline a request and verify resulting state."""
    endpoint = f"/api/v1/request/{request_id}/{action}"
    result = await call_overseerr_api(endpoint)
    expected = {"approve": _REQUEST_STATUS_APPROVED, "decline": _REQUEST_STATUS_DECLINED}.get(
        action
    )
    if expected is None:
        return

    got = _request_status_int(result.get("status"))
    if got is None:
        result = await call_overseerr_api(f"/api/v1/request/{request_id}", method="GET")
        got = _request_status_int(result.get("status"))

    if got != expected:
        raise Exception(
            f"Seerr request {request_id} {action} not confirmed: last known status={got!r} "
            f"(expected {expected})."
        )


def _webhook_request_id(data: dict) -> Optional[str]:
    req = data.get("request")
    if isinstance(req, dict):
        rid = req.get("request_id") or req.get("id")
        if rid is not None:
            return str(rid)
    top = data.get("request_id")
    if top is not None:
        return str(top)
    return None


async def _enrich_payload_with_seerr_request(data: dict) -> dict:
    nt = data.get("notification_type")
    if nt not in ("MEDIA_PENDING", "MEDIA_FAILED", "MEDIA_AVAILABLE"):
        return data

    rid = _webhook_request_id(data)
    if not rid:
        return data

    req_layer: dict[str, Any] = dict(data.get("request") or {})
    try:
        api_req = await call_overseerr_api(f"/api/v1/request/{rid}", method="GET")
    except Exception as err:
        logger.warning("Seerr request %s fetch failed (profile enrichment): %s", rid, err)
        return data

    merged_req = {**api_req, **req_layer}
    if merged_req.get("id") is not None and merged_req.get("request_id") is None:
        merged_req["request_id"] = merged_req["id"]
    merged_req = _normalize_request_keys(merged_req)

    pid = merged_req.get("profileId")
    sid = merged_req.get("serverId")
    name = merged_req.get("profileName") or merged_req.get("profile_name")
    media = merged_req.get("media") if isinstance(merged_req.get("media"), dict) else {}
    rtype = merged_req.get("type") or media.get("mediaType") or media.get("media_type") or "movie"

    if name is None and pid is not None and sid is not None:
        try:
            if rtype == "tv":
                svc = await call_overseerr_api(f"/api/v1/service/sonarr/{sid}", method="GET")
            else:
                svc = await call_overseerr_api(f"/api/v1/service/radarr/{sid}", method="GET")
            for p in svc.get("profiles") or []:
                if p.get("id") == pid:
                    merged_req["profileName"] = p.get("name")
                    break
        except Exception as err:
            logger.debug("Profile name resolve failed: %s", err)

    return {**data, "request": merged_req}


def _radarr_quality_and_folder(movie: dict) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(movie, dict):
        return None, None
    folder = movie.get("path")
    if not folder or not str(folder).strip():
        folder = None

    mf = movie.get("movieFile")
    if not isinstance(mf, dict):
        return None, folder

    quality = None
    q = mf.get("quality")
    if isinstance(q, dict):
        inner = q.get("quality")
        if isinstance(inner, dict):
            quality = inner.get("name")
        if not quality:
            quality = q.get("name")
    if isinstance(quality, str) and quality.strip():
        return quality.strip(), folder
    return None, folder


async def call_radarr_api(endpoint: str) -> Any:
    if not RADARR_API_KEY:
        raise ValueError("RADARR_API_KEY is not set")
    session = aiohttp.ClientSession()
    try:
        url = f"{RADARR_URL}{endpoint}"
        headers = {"X-Api-Key": RADARR_API_KEY}
        async with session.get(url, headers=headers) as response:
            if response.status >= 400:
                error_text = await response.text()
                raise Exception(f"Radarr API error {response.status}: {error_text}")
            return await response.json()
    finally:
        close_result = session.close()
        if hasattr(close_result, "__await__"):
            await close_result


async def _enrich_media_available_from_radarr(data: dict) -> dict:
    if data.get("notification_type") != "MEDIA_AVAILABLE":
        return data
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    media_type = media.get("media_type") or media.get("mediaType") or "movie"
    if media_type != "movie" or not RADARR_API_KEY:
        return data

    tmdb_raw = media.get("tmdbId") or media.get("tmdb_id")
    if tmdb_raw is None:
        return data
    try:
        tmdb_id = int(tmdb_raw)
    except (TypeError, ValueError):
        return data

    try:
        body = await call_radarr_api(f"/api/v3/movie?tmdbId={tmdb_id}")
    except Exception as err:
        logger.debug("Radarr movie lookup failed (tmdbId=%s): %s", tmdb_id, err)
        return data

    movie = body[0] if isinstance(body, list) and body else body if isinstance(body, dict) else None
    quality, folder = _radarr_quality_and_folder(movie or {})
    out = {**data}
    if quality:
        out["downloaded_quality"] = quality
    if folder:
        out["movie_folder"] = folder
    return out


@router.post("/api/v1/webhooks/overseerr")
@router.post("/api/v1/webhooks/seerr")
async def overseerr_webhook(request: Request, x_webhook_token: Optional[str] = Header(None)):
    """Handle Overseerr/Seerr webhook notifications."""
    if _sender is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sender not set"
        )

    try:
        try:
            raw_payload = await request.json()
        except Exception as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
            ) from err

        normalized_payload = _normalize_overseerr_payload(raw_payload)

        try:
            payload = OverseerrWebhook.model_validate(normalized_payload)
        except ValidationError as err:
            logger.error("Overseerr webhook validation error: %s", err)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid Overseerr webhook payload",
            ) from err

        webhook_token = _webhook_token_getter()
        require_valid_webhook_token(webhook_token, x_webhook_token)

        from app_config import WEBHOOK_HANDLERS

        handler_info = WEBHOOK_HANDLERS.get(payload.notification_type)
        if not handler_info:
            logger.info("Unhandled notification type: %s", payload.notification_type)
            return {"status": "ok"}

        working_payload = await _enrich_payload_with_seerr_request(normalized_payload)
        working_payload = await _enrich_media_available_from_radarr(working_payload)
        caption = handler_info["caption"](working_payload)

        reply_markup = None
        if handler_info["reply_markup"]:
            reply_markup = handler_info["reply_markup"](working_payload)

        await _sender(payload.image, caption, reply_markup)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Overseerr webhook handling error: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from err
