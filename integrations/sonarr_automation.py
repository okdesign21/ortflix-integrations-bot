"""Sonarr webhook automation routing and script execution."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app_config import WEBHOOK_TOKEN

from .automation_defaults import (
    PROJECT_SCRIPTS_DIR,
    canonical_sonarr_event_type,
    extract_event,
    resolve_timeout_seconds,
    sonarr_scripts_for_event,
)
from .automation_webhook import dispatch_automation_webhook
from .script_runner import run_python_script

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_webhook_token():
    return WEBHOOK_TOKEN


_webhook_token_getter = _default_webhook_token
SONARR_SCRIPT_DIR = PROJECT_SCRIPTS_DIR


def _sonarr_event_type(payload: dict) -> str:
    return canonical_sonarr_event_type(extract_event(payload, "eventType", "event"))


def configure_webhook_token_getter(getter):
    """Set webhook token accessor for runtime/test compatibility."""
    global _webhook_token_getter
    _webhook_token_getter = getter


def _resolve_sonarr_scripts_for_event(event_type: str) -> list[str]:
    return sonarr_scripts_for_event(event_type)


def _build_sonarr_script_env(payload: dict, event_type: str) -> dict[str, str]:
    env = os.environ.copy()
    series_raw = payload.get("series")
    episode_file_raw = payload.get("episodeFile")
    series: dict = series_raw if isinstance(series_raw, dict) else {}
    episode_file: dict = episode_file_raw if isinstance(episode_file_raw, dict) else {}

    env["sonarr_eventtype"] = event_type
    mapping = {
        "sonarr_series_id": series.get("id"),
        "sonarr_series_tvdbid": series.get("tvdbId"),
        "sonarr_series_imdbid": series.get("imdbId"),
        "sonarr_series_title": series.get("title"),
        "sonarr_series_year": series.get("year"),
        "sonarr_series_path": series.get("path"),
        "sonarr_series_originallanguage": (series.get("originalLanguage") or {}).get("name"),
        "sonarr_episodefile_path": episode_file.get("path"),
        "sonarr_episodefile_relativepath": episode_file.get("relativePath"),
    }

    for key, value in mapping.items():
        if value is not None:
            env[key] = str(value)
    return env


async def _execute_sonarr_script(script_name: str, payload: dict, event_type: str) -> None:
    script_path = SONARR_SCRIPT_DIR / script_name
    timeout = resolve_timeout_seconds("SONARR_SCRIPT_TIMEOUT")
    env = _build_sonarr_script_env(payload, event_type)
    await run_python_script(
        logger=logger,
        script_path=script_path,
        script_name=script_name,
        timeout_seconds=timeout,
        log_prefix="Sonarr",
        env=env,
    )


@router.post("/api/v1/webhooks/sonarr")
async def sonarr_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_token: Optional[str] = Header(None),
):
    """Handle Sonarr webhook events and dispatch automation scripts."""
    return await dispatch_automation_webhook(
        request=request,
        background_tasks=background_tasks,
        provided_webhook_token=x_webhook_token,
        expected_webhook_token=_webhook_token_getter(),
        payload_normalizer=lambda payload: payload if isinstance(payload, dict) else {},
        event_resolver=_sonarr_event_type,
        scripts_for_event=_resolve_sonarr_scripts_for_event,
        schedule_script=_execute_sonarr_script,
        component_label="Sonarr",
        missing_event_detail="Missing Sonarr event type",
        logger=logger,
    )
