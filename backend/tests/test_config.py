from __future__ import annotations

import pytest

from hia.config import Settings


def test_defaults_are_sane() -> None:
    settings = Settings(_env_file=None)
    assert settings.ha_url == "ws://localhost:8123"
    assert settings.log_level == "INFO"
    assert settings.reconnect_max_delay > settings.reconnect_initial_delay


def test_reads_from_hia_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIA_HA_URL", "http://homeassistant.local:8123")
    monkeypatch.setenv("HIA_HA_TOKEN", "secret-token")
    monkeypatch.setenv("HIA_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.ha_url == "http://homeassistant.local:8123"
    assert settings.ha_token == "secret-token"
    assert settings.log_level == "DEBUG"
