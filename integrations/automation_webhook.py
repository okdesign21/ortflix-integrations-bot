"""Shared webhook dispatcher for automation integrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Optional

from fastapi import BackgroundTasks, HTTPException, Request, status
from pydantic import ValidationError

from .webhook_auth import require_valid_webhook_token
from .webhook_logging import webhook_log_context


async def dispatch_automation_webhook(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    provided_webhook_token: Optional[str],
    expected_webhook_token: Optional[str],
    payload_normalizer: Callable[[Any], dict],
    event_resolver: Callable[[dict], str],
    scripts_for_event: Callable[[str], list[str]],
    schedule_script: Callable[[str, dict, str], Awaitable[None]],
    component_label: str,
    missing_event_detail: str,
    logger,
) -> dict:
    """Validate, normalize, and dispatch event scripts for automation webhooks."""
    try:
        try:
            raw_payload = await request.json()
        except Exception as parse_err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            ) from parse_err

        require_valid_webhook_token(expected_webhook_token, provided_webhook_token)

        try:
            payload = payload_normalizer(raw_payload)
        except ValidationError as err:
            logger.warning(
                "Webhook payload validation failed",
                extra=webhook_log_context(
                    request,
                    component_label.lower(),
                    event="webhook.validation_failed",
                    validation_error_count=len(err.errors()),
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid {component_label} payload",
            ) from err

        event_type = event_resolver(payload)
        if not event_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=missing_event_detail,
            )

        script_names = scripts_for_event(event_type)
        if not script_names:
            logger.info(
                "No scripts configured for webhook event",
                extra=webhook_log_context(
                    request,
                    component_label.lower(),
                    event="webhook.ignored",
                    event_type=event_type,
                    scheduled=0,
                ),
            )
            return {"status": "ignored", "event_type": event_type, "scheduled": 0}

        for script_name in script_names:
            background_tasks.add_task(schedule_script, script_name, payload, event_type)

        logger.info(
            "Webhook scripts scheduled",
            extra=webhook_log_context(
                request,
                component_label.lower(),
                event="webhook.accepted",
                event_type=event_type,
                scheduled=len(script_names),
            ),
        )
        return {
            "status": "accepted",
            "event_type": event_type,
            "scheduled": len(script_names),
            "scripts": script_names,
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(
            "Webhook handling failed",
            extra=webhook_log_context(
                request,
                component_label.lower(),
                event="webhook.failed",
                error_type=type(err).__name__,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from err
