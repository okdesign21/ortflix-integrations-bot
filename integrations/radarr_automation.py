"""Radarr webhook automation routing and script execution."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app_config import WEBHOOK_TOKEN

from .automation_defaults import (
    PROJECT_SCRIPTS_DIR,
    canonical_radarr_event_type,
    extract_event,
    radarr_scripts_for_event,
    resolve_timeout_seconds,
)
from .automation_webhook import dispatch_automation_webhook
from .payloads import _normalize_radarr_payload
from .script_runner import run_python_script

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_webhook_token():
    return WEBHOOK_TOKEN


_webhook_token_getter = _default_webhook_token

RADARR_SCRIPT_DIR = PROJECT_SCRIPTS_DIR


def configure_webhook_token_getter(getter):
    """Set webhook token accessor for runtime/test compatibility."""
    global _webhook_token_getter
    _webhook_token_getter = getter


def _radarr_event_type(payload: dict) -> str:
    """Normalize event field names from Radarr payloads."""
    return canonical_radarr_event_type(extract_event(payload, "eventType", "event"))


def _resolve_radarr_scripts_for_event(event_type: str) -> list[str]:
    return radarr_scripts_for_event(event_type)


def _build_radarr_script_env(payload: dict, event_type: str) -> dict[str, str]:
    """Build Radarr-script-compatible env vars from webhook payload."""
    env = os.environ.copy()
    movie_raw = payload.get("movie")
    movie_file_raw = payload.get("movieFile")
    movie: dict = movie_raw if isinstance(movie_raw, dict) else {}
    movie_file: dict = movie_file_raw if isinstance(movie_file_raw, dict) else {}

    env["radarr_eventtype"] = event_type

    mapping = {
        "radarr_movie_id": movie.get("id"),
        "radarr_movie_tmdbid": movie.get("tmdbId"),
        "radarr_movie_imdbid": movie.get("imdbId"),
        "radarr_movie_title": movie.get("title"),
        "radarr_movie_year": movie.get("year"),
        "radarr_movie_path": movie.get("path"),
        "radarr_movie_originallanguage": movie.get("originalLanguage"),
        "radarr_moviefile_path": movie_file.get("path"),
    }

    for key, value in mapping.items():
        if value is not None:
            env[key] = str(value)

    return env


async def _execute_radarr_script(script_name: str, payload: dict, event_type: str) -> None:
    """Run a Radarr script for the incoming webhook event."""
    script_path = RADARR_SCRIPT_DIR / script_name
    timeout = resolve_timeout_seconds("RADARR_SCRIPT_TIMEOUT")
    env = _build_radarr_script_env(payload, event_type)
    await run_python_script(
        logger=logger,
        script_path=script_path,
        script_name=script_name,
        timeout_seconds=timeout,
        log_prefix="Radarr",
        env=env,
    )


@router.post("/api/v1/webhooks/radarr")
async def radarr_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_token: Optional[str] = Header(None),
):
    """Handle Radarr webhook events and dispatch automation scripts."""
    return await dispatch_automation_webhook(
        request=request,
        background_tasks=background_tasks,
        provided_webhook_token=x_webhook_token,
        expected_webhook_token=_webhook_token_getter(),
        payload_normalizer=_normalize_radarr_payload,
        event_resolver=_radarr_event_type,
        scripts_for_event=_resolve_radarr_scripts_for_event,
        schedule_script=_execute_radarr_script,
        component_label="Radarr",
        missing_event_detail="Missing Radarr event type",
        logger=logger,
    )
