"""Basic tests for the telegram bot."""

import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient, ConnectError, MockTransport, Response
from telegram import Bot
from telegram.error import NetworkError
from telegram.request import HTTPXRequest

# Ensure fixtures from conf_test.py are discovered
pytest_plugins = ["conf_test"]

TEST_WEBHOOK_HEADERS = {"x-webhook-token": "test_webhook_token_123"}


@pytest.fixture(params=["INFO", "DEBUG"])
def secured_logging(request, monkeypatch):
    import bot as bot_module

    stream = io.StringIO()
    root = logging.getLogger()
    monkeypatch.setenv("LOG_LEVEL", request.param)
    monkeypatch.setattr(root, "level", getattr(logging, request.param))
    monkeypatch.setattr(root, "handlers", [logging.StreamHandler(stream)])
    for name in ("httpx", "httpcore", "telegram"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    bot_module._configure_logging()
    return bot_module, stream


@pytest.mark.asyncio
@pytest.mark.parametrize("request_fails", [False, True])
async def test_telegram_request_logs_do_not_expose_token(
    secured_logging, request_fails, monkeypatch
):
    bot_module, stream = secured_logging
    token = "123456:audit-synthetic-token"
    monkeypatch.setattr(bot_module, "TELEGRAM_TOKEN", token)

    def reply(request):
        if request_fails:
            raise ConnectError("Synthetic connection failure", request=request)
        return Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 123456,
                    "is_bot": True,
                    "first_name": "Audit",
                    "username": "audit_bot",
                },
            },
        )

    transport = HTTPXRequest(httpx_kwargs={"transport": MockTransport(reply)})
    updates = HTTPXRequest(httpx_kwargs={"transport": MockTransport(reply)})
    telegram_bot = Bot(token=token, request=transport, get_updates_request=updates)
    try:
        if request_fails:
            with pytest.raises(NetworkError):
                await telegram_bot.get_me()
        else:
            await telegram_bot.get_me()
    finally:
        await transport.shutdown()
        await updates.shutdown()

    bot_module.logger.info("Operator diagnostics remain available")
    text = stream.getvalue()
    assert "Operator diagnostics remain available" in text
    assert "HTTP Request" not in text
    assert token not in text


def test_log_formatter_redacts_tokens_in_messages_and_exceptions(secured_logging, monkeypatch):
    bot_module, stream = secured_logging
    token = "123456:audit-synthetic-token"
    other_token = "987654:another-synthetic-token"
    monkeypatch.setattr(bot_module, "TELEGRAM_TOKEN", token)
    try:
        raise RuntimeError(f"Request failed at https://api.telegram.org/bot{token}/getMe")
    except RuntimeError:
        bot_module.logger.exception("Request failed with token %s", token)

    dependency_logger = logging.getLogger("httpx")
    monkeypatch.setattr(dependency_logger, "level", logging.DEBUG)
    dependency_logger.debug("HTTP Request: https://api.telegram.org/bot%s/getMe", other_token)
    text = stream.getvalue()
    assert "RuntimeError" in text
    assert "[REDACTED]" in text
    assert token not in text
    assert other_token not in text


def test_production_startup_redacts_uvicorn_exception_handlers(secured_logging, monkeypatch):
    bot_module, stream = secured_logging
    token = "123456:production-logging-synthetic-token"
    monkeypatch.setattr(bot_module, "TELEGRAM_TOKEN", token)
    separate_output = io.StringIO()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(name)
        monkeypatch.setattr(server_logger, "handlers", [logging.StreamHandler(separate_output)])
        monkeypatch.setattr(server_logger, "propagate", False)

    def configure_server(application, **kwargs):
        bot_module.uvicorn.Config(application, **kwargs)
        for name in ("uvicorn.error", "uvicorn.access"):
            try:
                raise RuntimeError("Synthetic ASGI exception " + token)
            except RuntimeError:
                logging.getLogger(name).exception("Synthetic lifespan failure")

    monkeypatch.setattr(bot_module.uvicorn, "run", configure_server)
    bot_module.main()
    text = stream.getvalue()
    assert "Synthetic ASGI exception" in text
    assert "[REDACTED]" in text
    assert token not in text
    assert not separate_output.getvalue()


@pytest.mark.asyncio
async def test_send_photo_or_message_with_photo(mock_telegram_bot, mock_env):
    """Test sending a photo message."""
    import bot as bot_module

    bot_module.bot = mock_telegram_bot

    from bot import send_photo_or_message

    image = "https://example.com/poster.jpg"
    caption = "Test caption"

    with patch("bot.TELEGRAM_CHAT_ID", 12345):
        await send_photo_or_message(image, caption)

    mock_telegram_bot.send_photo.assert_called_once()
    assert mock_telegram_bot.send_photo.call_args[1]["photo"] == image
    assert mock_telegram_bot.send_photo.call_args[1]["caption"] == caption


@pytest.mark.asyncio
async def test_send_photo_or_message_without_photo(mock_telegram_bot, mock_env):
    """Test sending a text-only message."""
    import bot as bot_module

    bot_module.bot = mock_telegram_bot

    from bot import send_photo_or_message

    caption = "Test message"

    with patch("bot.TELEGRAM_CHAT_ID", 12345):
        await send_photo_or_message(None, caption)

    mock_telegram_bot.send_message.assert_called_once()
    assert mock_telegram_bot.send_message.call_args[1]["text"] == caption


@pytest.mark.asyncio
async def test_call_overseerr_approve(mock_env):
    """Test calling Overseerr API to approve a request."""
    from bot import call_overseerr

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value='{"status":2,"id":123}')
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.__aexit__ = AsyncMock()

    with patch("aiohttp.ClientSession") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock()
        mock_client.return_value.__aexit__ = AsyncMock()
        mock_client.return_value.post.return_value.__aenter__ = AsyncMock(
            return_value=mock_response
        )
        mock_client.return_value.post.return_value.__aexit__ = AsyncMock()

        await call_overseerr("123", "approve")

        mock_client.return_value.post.assert_called_once()
        call_args = mock_client.return_value.post.call_args
        assert "123" in call_args[0][0]
        assert "approve" in call_args[0][0]


def test_environment_variables_required(monkeypatch):
    """Test that config validation catches missing required environment variables."""
    import sys

    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OVERSEERR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    # Reload config module to pick up deleted env vars
    if "app_config" in sys.modules:
        del sys.modules["app_config"]

    from app_config import validate_config

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        validate_config()


def test_webhook_token_required_unless_explicit_override(monkeypatch):
    """Webhook token must be configured unless insecure mode is explicitly allowed."""
    import sys

    monkeypatch.setenv("TELEGRAM_TOKEN", "test_token")
    monkeypatch.setenv("OVERSEERR_API_KEY", "test_api_key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_WEBHOOKS", raising=False)

    if "app_config" in sys.modules:
        del sys.modules["app_config"]

    from app_config import validate_config

    with pytest.raises(ValueError, match="WEBHOOK_TOKEN is not set"):
        validate_config()


def test_webhook_token_override_allows_insecure_mode(monkeypatch):
    """Explicit insecure override should allow startup without webhook token."""
    import sys

    monkeypatch.setenv("TELEGRAM_TOKEN", "test_token")
    monkeypatch.setenv("OVERSEERR_API_KEY", "test_api_key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("WEBHOOK_TOKEN", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "true")

    if "app_config" in sys.modules:
        del sys.modules["app_config"]

    from app_config import validate_config

    validate_config()


def test_webhook_payload_validation(sample_webhook_payload):
    """Test that webhook payloads are properly structured."""
    assert "notification_type" in sample_webhook_payload
    assert "request" in sample_webhook_payload
    assert "request_id" in sample_webhook_payload["request"]


@pytest.mark.asyncio
async def test_health_check_endpoint(mock_env, mock_telegram_bot):
    """Test the health check endpoint."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "bot_initialized" in data
    assert "telegram_connected" in data


@pytest.mark.asyncio
async def test_overseerr_webhook_pending(mock_env, mock_telegram_bot, sample_webhook_payload):
    """Test Overseerr webhook for pending media request."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/overseerr",
            json=sample_webhook_payload,
            headers=TEST_WEBHOOK_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_telegram_bot.send_photo.assert_called_once()


@pytest.mark.asyncio
async def test_seerr_webhook_alias_pending(mock_env, mock_telegram_bot, sample_webhook_payload):
    """Test Seerr alias route for pending media request."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/seerr",
            json=sample_webhook_payload,
            headers=TEST_WEBHOOK_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_telegram_bot.send_photo.assert_called_once()


@pytest.mark.asyncio
async def test_overseerr_webhook_with_token(mock_env, mock_telegram_bot, sample_webhook_payload):
    """Test Overseerr webhook with authentication token."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    with patch("bot.WEBHOOK_TOKEN", "secret_token_123"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test without token - should fail
            response = await client.post("/api/v1/webhooks/overseerr", json=sample_webhook_payload)
            assert response.status_code == 401

            # Test with correct token - should succeed
            response = await client.post(
                "/api/v1/webhooks/overseerr",
                json=sample_webhook_payload,
                headers={"x-webhook-token": "secret_token_123"},
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_radarr_webhook_ignored_event(mock_env, mock_telegram_bot):
    """Events with no configured scripts should return ignored."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    payload = {
        "eventType": "Rename",
        "movie": {"id": 10, "title": "Test Movie"},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/radarr", json=payload, headers=TEST_WEBHOOK_HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert body["event_type"] == "Rename"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,expected_event_type,expected_script",
    [
        ("MovieAdded", "MovieAdded", "route_hdr_on_import_radarr.py"),
        ("File Import", "Download", "tag_overseerr_requester_radarr.py"),
        ("File Upgrade", "Download", "tag_overseerr_requester_radarr.py"),
    ],
)
async def test_radarr_webhook_accepted_event(
    mock_env, mock_telegram_bot, event_type, expected_event_type, expected_script
):
    """Mapped Radarr events should schedule configured scripts."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    payload = {
        "eventType": event_type,
        "movie": {"id": 10, "title": "Test Movie"},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/radarr", json=payload, headers=TEST_WEBHOOK_HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["event_type"] == expected_event_type
    expected_count = 1 if expected_script == "route_hdr_on_import_radarr.py" else 2
    assert body["scheduled"] == expected_count
    assert expected_script in body["scripts"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,expected_event_type,expected_script",
    [
        ("Download", "Download", "tag_overseerr_requester_sonarr.py"),
        ("Import", "Download", "tag_overseerr_requester_sonarr.py"),
        ("Series Add", "SeriesAdd", "route_hdr_on_import_sonarr.py"),
    ],
)
async def test_sonarr_webhook_accepts_event(
    mock_env, mock_telegram_bot, event_type, expected_event_type, expected_script
):
    """Sonarr router should accept native and UI-style event labels."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    payload = {
        "eventType": event_type,
        "series": {"id": 20, "title": "Test Show"},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/sonarr", json=payload, headers=TEST_WEBHOOK_HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["event_type"] == expected_event_type
    assert expected_script in body["scripts"]


@pytest.mark.asyncio
async def test_media_integrity_webhook(mock_env, mock_telegram_bot):
    """Test media integrity check webhook."""
    import bot as bot_module
    from bot import app

    bot_module.bot = mock_telegram_bot
    bot_module.app_telegram = MagicMock()

    payload = {
        "notification_type": "CORRUPTION_DETECTED",
        "summary_message": "🔴 *Media Integrity Alert*\n\n3 corrupted files detected",
        "count": 3,
        "files": [
            {"path": "/media/movie1.mkv", "size": "1.2GB", "error": "Container error"},
            {"path": "/media/movie2.mkv", "size": "2.4GB", "error": "Codec issue"},
            {"path": "/media/show.mkv", "size": "800MB", "error": "Truncated file"},
        ],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/webhooks/media-check",
            json=payload,
            headers=TEST_WEBHOOK_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_telegram_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_pydantic_models():
    """Test Pydantic model validation."""
    from integrations.models import CorruptedFileInfo, MediaIntegrityWebhook, OverseerrWebhook

    # Test OverseerrWebhook model
    webhook = OverseerrWebhook(notification_type="MEDIA_PENDING", subject="Test Movie")
    assert webhook.notification_type == "MEDIA_PENDING"
    assert webhook.subject == "Test Movie"

    # Test MediaIntegrityWebhook model
    corrupted_file = CorruptedFileInfo(path="/test/file.mkv", size="1GB", error="Test error")
    media_webhook = MediaIntegrityWebhook(
        notification_type="CORRUPTION_DETECTED", count=1, files=[corrupted_file]
    )
    assert media_webhook.count == 1
    assert len(media_webhook.files) == 1
