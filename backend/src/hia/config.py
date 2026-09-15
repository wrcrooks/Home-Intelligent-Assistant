"""Runtime settings.

Loaded from environment variables (and a local ``.env`` file in development).
Under the add-on, these are populated from the Supervisor's ``/data/options.json``
instead — that adapter is added in P10 packaging, not here. Keeping settings behind
one object now means that swap touches this file only.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for connecting to and interacting with Home Assistant."""

    model_config = SettingsConfigDict(
        env_prefix="HIA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ha_url: str = Field(
        default="ws://localhost:8123",
        description=(
            "Base URL of the Home Assistant instance, e.g. http://homeassistant.local:8123 "
            "or ws://homeassistant.local:8123. The websocket path (/api/websocket) is "
            "appended automatically."
        ),
    )
    ha_token: str = Field(
        default="",
        description="Long-lived access token (or SUPERVISOR_TOKEN under the add-on).",
    )

    log_level: str = "INFO"
    log_json: bool = False
    """Structured JSON logs for production/add-on use; human-readable console output
    for local development. Overridden to True automatically when SUPERVISOR_TOKEN is
    present (i.e. running as an add-on) unless explicitly set."""

    reconnect_initial_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    reconnect_backoff_factor: float = 2.0

    event_queue_max_size: int = 2000
    """Bounded queue between the websocket read loop and event consumers. See
    docs/02-architecture.md's note on backpressure: a slow consumer must never stall
    the read loop, so the queue drops the newest event and logs a counter rather than
    blocking when full."""


def get_settings() -> Settings:
    """Load settings fresh from the environment.

    Not cached at module scope: tests and the CLI both want to construct
    ``Settings`` with different environments/argument overrides without import-order
    surprises.
    """
    return Settings()
