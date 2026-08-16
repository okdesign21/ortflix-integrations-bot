"""Tests for shared Plex configuration helpers."""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import integration_utils  # noqa: E402


def test_resolve_plex_url_prefers_shared_name(monkeypatch):
    monkeypatch.setenv("PLEX_URL", "http://plex-shared:32400/")

    assert integration_utils.resolve_plex_url("http://localhost:32400") == "http://plex-shared:32400"


def test_resolve_plex_url_uses_config_default(monkeypatch):
    monkeypatch.delenv("PLEX_URL", raising=False)

    assert integration_utils.resolve_plex_url("http://config-plex:32400") == "http://config-plex:32400"


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
