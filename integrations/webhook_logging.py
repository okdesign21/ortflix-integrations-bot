"""Structured logging context helpers for incoming webhooks."""

from typing import Any

from fastapi import Request


def webhook_log_context(request: Request | None, component: str, **details: Any) -> dict[str, Any]:
    """Build consistent safe context fields for webhook log records."""
    context = {"component": component, **details}
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    if request_id:
        context["request_id"] = request_id
    return context
