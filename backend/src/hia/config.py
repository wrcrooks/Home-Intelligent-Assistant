"""Runtime settings.

Loaded from environment variables (and a local ``.env`` file in development).
Under the add-on, these are populated from the Supervisor's ``/data/options.json``
instead — that adapter is added in P10 packaging, not here. Keeping settings behind
one object now means that swap touches this file only.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for connecting to and interacting with Home Assistant."""

    model_config = SettingsConfigDict(
        env_prefix="HIA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supervisor_token: str = Field(default="", validation_alias="SUPERVISOR_TOKEN")
    """Set automatically by the Supervisor for add-ons that request `hassio_api:
    true` — deliberately *not* HIA_-prefixed, since this is Supervisor-owned, not
    ours to name. Its presence is how this process knows it's running as an add-on
    rather than in local dev."""

    @property
    def is_addon(self) -> bool:
        return bool(self.supervisor_token)

    @model_validator(mode="after")
    def _default_log_json_when_addon(self) -> Settings:
        if self.is_addon and "log_json" not in self.model_fields_set:
            self.log_json = True
        return self

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

    data_dir: str = "./.data"
    """Where the event store (and later, model artifacts) live. Under the add-on this
    is a persistent volume path; locally it's a gitignored directory under backend/."""

    ingest_event_types: list[str] = [
        "state_changed",
        "automation_triggered",
        "script_started",
        "call_service",
    ]
    """Subscribed alongside state_changed from day one, per docs/04-roadmap.md P1 and
    docs/05-provenance.md §7: automation firings can't be attributed retroactively, so
    they're captured now even though nothing interprets them until P3."""

    recorder_db_path: str = ""
    """Path to Home Assistant's recorder database (typically
    home-assistant_v2.db inside the HA config directory) for `hia backfill`. SQLite
    only — see hia.ingest.backfill's module docstring for why MariaDB/Postgres
    aren't wired up yet despite the reader being built to support them."""

    api_host: str = "0.0.0.0"
    api_port: int = 8099
    """8099 matches the conventional default `ingress_port` for HA add-ons
    (docs/HANDOFF.md platform facts) — not required, but means the add-on manifest
    won't need to override it later."""


def get_settings() -> Settings:
    """Load settings fresh from the environment.

    Not cached at module scope: tests and the CLI both want to construct
    ``Settings`` with different environments/argument overrides without import-order
    surprises.
    """
    return Settings()
