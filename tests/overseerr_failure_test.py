"""External API degradation tests for Overseerr integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from integrations.overseerr_integration import call_overseerr_api


@pytest.mark.asyncio
async def test_call_overseerr_api_raises_on_upstream_http_error():
    response = MagicMock(status=503)
    response.text = AsyncMock(return_value="upstream unavailable")
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    request_context.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = request_context
    session.close.return_value = None

    with patch("integrations.overseerr_integration.aiohttp.ClientSession", return_value=session):
        with pytest.raises(Exception, match="Overseerr API error 503"):
            await call_overseerr_api("/api/v1/request")

    session.close.assert_called_once()
    response.text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_overseerr_api_propagates_timeout_and_closes_session():
    session = MagicMock()
    session.post.side_effect = asyncio.TimeoutError()
    session.close.return_value = None

    with patch("integrations.overseerr_integration.aiohttp.ClientSession", return_value=session):
        with pytest.raises(asyncio.TimeoutError):
            await call_overseerr_api("/api/v1/request")

    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_call_overseerr_api_propagates_client_error_and_closes_session():
    session = MagicMock()
    session.post.side_effect = aiohttp.ClientConnectionError("connection refused")
    session.close.return_value = None

    with patch("integrations.overseerr_integration.aiohttp.ClientSession", return_value=session):
        with pytest.raises(aiohttp.ClientConnectionError):
            await call_overseerr_api("/api/v1/request")

    session.close.assert_called_once()
