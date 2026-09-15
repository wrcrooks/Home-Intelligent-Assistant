"""The `/api/ws/events` route: connects, is tracked, and disconnects cleanly.

The relay's actual event logic (writing durably, broadcasting state_changed only)
is tested in isolation in test_state.py — see that module's docstring for why a
full end-to-end test through this route (a real background `asyncio.to_thread`
DuckDB write racing TestClient's own portal thread) isn't automated here. This test
sticks to what's safe to automate: the route accepts a connection and the
ConnectionManager tracks it, using the same unreachable-ha_url pattern as
test_app.py so the background ingest task never has anything to relay and never
touches the store.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hia.api.app import create_app
from hia.api.state import AppState
from hia.config import Settings
from hia.ingest.store import EventStore


def _settings(data_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(data_dir),
        ha_url="http://127.0.0.1:1",  # nothing listens here
        ha_token="unused",
        reconnect_initial_delay=0.01,
        reconnect_max_delay=0.02,
    )


def test_websocket_connects_and_is_tracked_then_untracked_on_disconnect(
    tmp_path: Path,
) -> None:
    EventStore(tmp_path / "hia.duckdb").close()
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        state: AppState = app.state.hia
        assert state.manager.connection_count == 0

        with client.websocket_connect("/api/ws/events"):
            assert state.manager.connection_count == 1

        assert state.manager.connection_count == 0
