"""Tests for shared Plex configuration helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import integration_utils  # noqa: E402


def test_resolve_plex_url_prefers_shared_name(monkeypatch):
    monkeypatch.setenv("PLEX_URL", "http://plex-shared:32400/")

    assert (
        integration_utils.resolve_plex_url("http://localhost:32400") == "http://plex-shared:32400"
    )


def test_resolve_plex_url_uses_config_default(monkeypatch):
    monkeypatch.delenv("PLEX_URL", raising=False)

    assert (
        integration_utils.resolve_plex_url("http://config-plex:32400") == "http://config-plex:32400"
    )


def test_resolve_plex_token_prefers_file(monkeypatch, tmp_path: Path):
    token_file = tmp_path / "plex-token.txt"
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("PLEX_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PLEX_TOKEN", "env-token")

    assert integration_utils.resolve_plex_token("/unused/default") == "file-token"


def test_resolve_plex_token_uses_env_value(monkeypatch):
    monkeypatch.delenv("PLEX_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PLEX_TOKEN", "env-token")

    assert integration_utils.resolve_plex_token("/unused/default") == "env-token"


def test_resolve_plex_token_rejects_missing_explicit_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PLEX_TOKEN_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("PLEX_TOKEN", "private-token")

    with pytest.raises(SystemExit):
        integration_utils.resolve_plex_token("/unused/default")
    output = capsys.readouterr()
    assert "PLEX_TOKEN_FILE" in output.err
    assert "private-token" not in output.err
    assert not output.out


@pytest.mark.parametrize("name", sorted(integration_utils.SECRET_NAMES))
def test_script_credentials_prefer_files(name, tmp_path, monkeypatch):
    secret_file = tmp_path / "credential"
    secret_file.write_text(" script-secret\n", encoding="utf-8")
    monkeypatch.setenv(name, "environment-secret")
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))
    assert integration_utils._env(name, required=True) == "script-secret"


@pytest.mark.parametrize("service", ["telegram", "discord"])
@pytest.mark.parametrize("failure", ["exception", "response"])
def test_notification_files_and_safe_errors(service, failure, tmp_path, monkeypatch, capsys):
    name = "TELEGRAM_TOKEN" if service == "telegram" else "DISCORD_WEBHOOK_URL"
    secret = "private-secret"
    secret_file = tmp_path / "credential"
    secret_file.write_text(secret, encoding="utf-8")
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN_FILE", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    post = Mock(return_value=Mock(status_code=401, ok=False, text=secret))
    if failure == "exception":
        post.side_effect = integration_utils.requests.RequestException(secret)
    monkeypatch.setattr(integration_utils.requests, "post", post)

    if service == "telegram":
        integration_utils.telegram_post("test")
        assert post.call_args.args[0] == f"https://api.telegram.org/bot{secret}/sendMessage"
    else:
        integration_utils.discord_post("test")
        assert post.call_args.args[0] == secret
    output = capsys.readouterr()
    assert "[WARN]" in output.err
    assert secret not in output.err
    assert not output.out


def test_overseerr_script_accepts_legacy_key_file(tmp_path, monkeypatch):
    for name in ("OVERSEERR_API_KEY", "SEERR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    secret_file = tmp_path / "credential"
    secret_file.write_text("legacy-key\n", encoding="utf-8")
    monkeypatch.setenv("API_KEY_FILE", str(secret_file))
    assert integration_utils.overseerr_api_key() == "legacy-key"


def test_service_urls_use_config_defaults(monkeypatch):
    monkeypatch.delenv("RADARR_URL", raising=False)
    monkeypatch.delenv("SONARR_URL", raising=False)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)
    monkeypatch.delenv("SEERR_URL", raising=False)
    monkeypatch.setattr(
        integration_utils,
        "load_bot_settings",
        lambda: {
            "shared": {
                "services": {
                    "radarr": {"url": "http://cfg-radarr:7878"},
                    "sonarr": {"url": "http://cfg-sonarr:8989"},
                    "overseerr": {"url": "http://cfg-overseerr:5055"},
                }
            }
        },
    )

    assert integration_utils.radarr_base_url() == "http://cfg-radarr:7878"
    assert integration_utils.sonarr_base_url() == "http://cfg-sonarr:8989"
    assert integration_utils.overseerr_base_url() == "http://cfg-overseerr:5055"
