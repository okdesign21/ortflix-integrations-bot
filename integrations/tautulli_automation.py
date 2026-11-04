"""Tautulli webhook automation routing and script execution."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import ValidationError

from app_config import WEBHOOK_TOKEN

from .automation_defaults import (
    PROJECT_SCRIPTS_DIR,
    extract_event,
    normalize_event_token,
    resolve_timeout_seconds,
    tautulli_script_for_event,
)
from .payloads import _normalize_tautulli_payload
from .script_runner import run_python_script
from .webhook_auth import require_valid_webhook_token
from .webhook_logging import webhook_log_context

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_webhook_token():
    return WEBHOOK_TOKEN


_webhook_token_getter = _default_webhook_token
TAUTULLI_SCRIPT_DIR = PROJECT_SCRIPTS_DIR


def configure_webhook_token_getter(getter):
    global _webhook_token_getter
    _webhook_token_getter = getter


def _normalize_event(value: str) -> str:
    return normalize_event_token(value)


def _event_from_payload(payload: dict) -> str:
    event = extract_event(payload, "event", "trigger", "notification_type")
    return _normalize_event(event)


def _script_args_for_event(event: str, payload: dict) -> list[str]:
    # Supports both Tautulli arg keys and normalized webhook keys.
    tmdb = payload.get("themoviedb_id") or payload.get("tmdb_id") or payload.get("tmdbId")
    imdb = payload.get("imdb_id") or payload.get("imdbId")
    title = payload.get("title")
    year = payload.get("year")
    username = payload.get("username") or payload.get("user")

    args = []
    if tmdb is not None:
        args += ["--themoviedb_id", str(tmdb)]
    if imdb is not None:
        args += ["--imdb_id", str(imdb)]
    if title:
        args += ["--title", str(title)]
    if year is not None:
        args += ["--year", str(year)]
    if event == "watched" and username:
        args += ["--username", str(username)]
    return args


async def _execute_tautulli_script(script_name: str, event: str, payload: dict) -> None:
    script_path = TAUTULLI_SCRIPT_DIR / script_name
    timeout = resolve_timeout_seconds("TAUTULLI_SCRIPT_TIMEOUT")
    args = _script_args_for_event(event, payload)
    env = os.environ.copy()
    await run_python_script(
        logger=logger,
        script_path=script_path,
        script_name=script_name,
        timeout_seconds=timeout,
        log_prefix="Tautulli",
        env=env,
        args=args,
    )


@router.post("/api/v1/webhooks/tautulli")
async def tautulli_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_token: Optional[str] = Header(None),
):
    """Handle Tautulli events and dispatch scripts."""
    try:
        try:
            payload = await request.json()
        except Exception as parse_err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            ) from parse_err

        webhook_token = _webhook_token_getter()
        require_valid_webhook_token(webhook_token, x_webhook_token)

        try:
            payload = _normalize_tautulli_payload(payload)
        except ValidationError as err:
            logger.warning(
                "Webhook payload validation failed",
                extra=webhook_log_context(
                    request,
                    "tautulli",
                    event="webhook.validation_failed",
                    validation_error_count=len(err.errors()),
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid Tautulli payload",
            ) from err

        event = _event_from_payload(payload)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing Tautulli event",
            )

        script_name = tautulli_script_for_event(event)
        if not script_name:
            logger.info(
                "No script configured for webhook event",
                extra=webhook_log_context(
                    request,
                    "tautulli",
                    event="webhook.ignored",
                    event_type=event,
                    scheduled=0,
                ),
            )
            return {"status": "ignored", "event": event, "scheduled": 0}

        background_tasks.add_task(_execute_tautulli_script, script_name, event, payload)
        logger.info(
            "Webhook scripts scheduled",
            extra=webhook_log_context(
                request,
                "tautulli",
                event="webhook.accepted",
                event_type=event,
                scheduled=1,
            ),
        )
        return {
            "status": "accepted",
            "event": event,
            "scheduled": 1,
            "scripts": [script_name],
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(
            "Webhook handling failed",
            extra=webhook_log_context(
                request,
                "tautulli",
                event="webhook.failed",
                error_type=type(err).__name__,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from err
