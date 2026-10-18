"""Secret file loading and startup configuration regression tests."""

import runpy
from pathlib import Path

import pytest

from integrations.secret_config import SECRET_NAMES as ALL_SECRET_NAMES
from integrations.secret_config import load_secret

SECRET_NAMES = (
    "TELEGRAM_TOKEN",
    "OVERSEERR_API_KEY",
    "API_KEY",
    "RADARR_API_KEY",
    "SONARR_API_KEY",
    "WEBHOOK_TOKEN",
)


@pytest.fixture(autouse=True)
def clear_secrets(monkeypatch):
    for name in ALL_SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


def config_values():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "app_config.py"))


@pytest.mark.parametrize("name", SECRET_NAMES)
@pytest.mark.parametrize("with_environment", [False, True])
def test_file_loading_takes_precedence(name, with_environment, tmp_path, monkeypatch):
    secret_file = tmp_path / "credential"
    secret_file.write_text(" \tfile-secret\r\n", encoding="utf-8")
    if with_environment:
        monkeypatch.setenv(name, "environment-secret")
    monkeypatch.setenv(f"{name}_FILE", str(secret_file))

    target = "OVERSEERR_API_KEY" if name == "API_KEY" else name
    assert config_values()[target] == "file-secret"


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_environment_fallback(name, monkeypatch):
    monkeypatch.setenv(name, "environment-secret")

    target = "OVERSEERR_API_KEY" if name == "API_KEY" else name
    assert config_values()[target] == "environment-secret"


def test_unconfigured_secret():
    assert load_secret("TELEGRAM_TOKEN") is None


def test_canonical_overseerr_key_precedes_legacy_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "legacy-key"
    secret_file.write_text("legacy-key", encoding="utf-8")
    monkeypatch.setenv("OVERSEERR_API_KEY", "canonical-key")
    monkeypatch.setenv("API_KEY_FILE", str(secret_file))
    assert config_values()["OVERSEERR_API_KEY"] == "canonical-key"


@pytest.mark.parametrize("name", sorted(ALL_SECRET_NAMES))
def test_invalid_optional_or_shadowed_file_fails_startup(name, tmp_path, monkeypatch):
    monkeypatch.setenv("OVERSEERR_API_KEY", "canonical-key")
    monkeypatch.setenv(f"{name}_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match=f"{name}_FILE"):
        config_values()


@pytest.mark.parametrize(
    "failure", ["missing", "directory", "empty", "whitespace", "utf8", "blank"]
)
def test_invalid_file_fails_startup(failure, tmp_path, monkeypatch):
    secret_file = tmp_path / "private-path"
    if failure == "directory":
        secret_file.mkdir()
    elif failure == "empty":
        secret_file.write_text("", encoding="utf-8")
    elif failure == "whitespace":
        secret_file.write_text(" \n\t", encoding="utf-8")
    elif failure == "utf8":
        secret_file.write_bytes(b"private-secret\xff")
    monkeypatch.setenv("TELEGRAM_TOKEN", "environment-secret")
    monkeypatch.setenv("TELEGRAM_TOKEN_FILE", "" if failure == "blank" else str(secret_file))

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN_FILE") as error:
        config_values()
    assert "private" not in str(error.value)
    assert "environment-secret" not in str(error.value)
    assert error.value.__suppress_context__ or failure in {"empty", "whitespace", "blank"}


def test_unreadable_file_suppresses_underlying_error(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("private-secret private-path")

    monkeypatch.setenv("TELEGRAM_TOKEN_FILE", str(tmp_path / "credential"))
    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ValueError, match="TELEGRAM_TOKEN_FILE") as error:
        load_secret("TELEGRAM_TOKEN")
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("token", [" ", "\t", "\r\n", " \t\n "])
@pytest.mark.parametrize("insecure", ["true", "false"])
def test_whitespace_webhook_token_rejected_at_startup(token, insecure, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("OVERSEERR_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("WEBHOOK_TOKEN", token)
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", insecure)

    with pytest.raises(ValueError, match="^WEBHOOK_TOKEN must not contain only whitespace$"):
        config_values()["validate_config"]()


@pytest.mark.parametrize("token", [None, ""])
@pytest.mark.parametrize("insecure", ["true", "false"])
def test_missing_webhook_token_still_requires_explicit_override(token, insecure, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("OVERSEERR_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", insecure)
    if token is not None:
        monkeypatch.setenv("WEBHOOK_TOKEN", token)

    validate = config_values()["validate_config"]
    if insecure == "true":
        validate()
    else:
        with pytest.raises(ValueError, match="WEBHOOK_TOKEN is not set"):
            validate()


@pytest.mark.parametrize("source", ["environment", "file"])
def test_valid_webhook_token_passes_startup(source, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("OVERSEERR_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "false")
    if source == "file":
        secret_file = tmp_path / "webhook-token"
        secret_file.write_text(" test-webhook-token\n", encoding="utf-8")
        monkeypatch.setenv("WEBHOOK_TOKEN_FILE", str(secret_file))
    else:
        monkeypatch.setenv("WEBHOOK_TOKEN", "test-webhook-token")

    config_values()["validate_config"]()
