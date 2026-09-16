"""hia.api.actors and the ``/api/provenance/actors`` routes (Layer 3's setup
task, docs/05-provenance.md §4).

``build_actors_payload`` (the ``GET`` route's logic) is tested directly here,
not by driving it through a real ``GET`` via ``TestClient`` — reproduced live on
this dev machine, not assumed: doing so hangs indefinitely (caught by
pytest-timeout, not a bug in the route itself — confirmed by calling
``build_actors_payload`` directly against the same fake server, which passes in
well under a second). Same root category as the reason ``test_state.py`` tests
``AppState.ingest_and_relay`` in isolation rather than through
``/api/ws/events``: ``TestClient``'s cross-thread portal bridge and a route
handler that itself performs genuine outbound async I/O (here, a fresh
``HomeAssistantClient`` connecting out to fetch the user registry) don't mix
cleanly in this environment. The route wiring itself (that ``GET
/api/provenance/actors`` calls this function via ``state.new_client()``) is
simple enough to trust from reading it; what's worth testing thoroughly is the
merge logic, and that's exactly what's isolated here.

The ``POST`` route never makes an outbound HA call at all (it only writes to the
store), so it's tested the normal way, through a real ``TestClient`` request.
"""

from __future__ import annotations

from pathlib import Path

import aiohttp
from fastapi.testclient import TestClient

from hia.api.actors import build_actors_payload
from hia.api.app import create_app
from hia.config import Settings
from hia.ha.client import HomeAssistantClient
from hia.ingest.store import EventStore
from tests.conftest import VALID_TOKEN, FakeHomeAssistant
from tests.ingest.factories import make_generic_watched


def _settings(data_dir: Path, ha_url: str = "http://127.0.0.1:1") -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(data_dir),
        ha_url=ha_url,
        ha_token=VALID_TOKEN,
        reconnect_initial_delay=0.01,
        reconnect_max_delay=0.02,
        frontend_dist_dir="__no_frontend_dist_for_tests__",
    )


async def test_build_actors_payload_merges_ha_users_with_observed_events_and_suggestions(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    server.set_registry_response(
        "config/auth/list",
        [
            {"id": "user-will", "name": "Will", "system_generated": False},
            {"id": "user-nodered", "name": "Node-RED", "system_generated": False},
        ],
    )
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_generic_watched(1, "call_service", {}, context_user_id="user-will")
        )
        store.set_actor_classification("user-will", "human")

        async with aiohttp.ClientSession() as session:
            client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
            rows = await build_actors_payload(client, store)

    by_id = {r.user_id: r for r in rows}
    assert by_id["user-will"].confirmed_class == "human"
    assert by_id["user-will"].event_count == 1
    assert by_id["user-nodered"].suggested_class == "service_account"
    assert by_id["user-nodered"].confirmed_class is None
    assert by_id["user-nodered"].event_count == 0


async def test_build_actors_payload_flags_a_user_id_not_in_the_ha_registry(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    """A stale/deleted account, or a registry fetch that degraded (a non-admin
    token, hia.ha.registry.fetch_users) -- the event history is still real and
    worth surfacing even without a name to attach to it."""
    server, base_url = fake_ha
    server.set_registry_response("config/auth/list", [])
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_generic_watched(1, "call_service", {}, context_user_id="user-ghost")
        )
        async with aiohttp.ClientSession() as session:
            client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
            rows = await build_actors_payload(client, store)

    assert len(rows) == 1
    assert rows[0].user_id == "user-ghost"
    assert "not found" in rows[0].suggested_reason


def test_confirm_actor_persists(tmp_path: Path) -> None:
    EventStore(tmp_path / "hia.duckdb").close()

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post("/api/provenance/actors/user-will", json={"actor_class": "human"})

    assert response.status_code == 200
    with EventStore(tmp_path / "hia.duckdb", read_only=True) as store:
        assert store.actor_classifications() == {"user-will": "human"}


def test_confirm_actor_rejects_an_invalid_actor_class(tmp_path: Path) -> None:
    EventStore(tmp_path / "hia.duckdb").close()

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/api/provenance/actors/user-will", json={"actor_class": "not_a_real_class"}
        )

    assert response.status_code == 422
