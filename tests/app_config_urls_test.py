"""Tests for app_config service URL defaults."""

from __future__ import annotations

import importlib
import sys


def test_app_config_uses_bot_settings_service_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "bot_settings.yaml"
    config_path.write_text(
        """
shared:
  services:
    overseerr:
      url: http://cfg-overseerr:5055
    radarr:
      url: http://cfg-radarr:7878
    sonarr:
      url: http://cfg-sonarr:8989
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("ORTFLIX_BOT_SETTINGS_FILE", str(config_path))
    monkeypatch.delenv("OVERSEERR_URL", raising=False)
    monkeypatch.delenv("RADARR_URL", raising=False)
    monkeypatch.delenv("SONARR_URL", raising=False)
    monkeypatch.delenv("SEERR_URL", raising=False)

    if "app_config" in sys.modules:
        del sys.modules["app_config"]

    app_config = importlib.import_module("app_config")

    assert app_config.OVERSEERR_URL == "http://cfg-overseerr:5055"
    assert app_config.RADARR_URL == "http://cfg-radarr:7878"
    assert app_config.SONARR_URL == "http://cfg-sonarr:8989"
