"""REST endpoints in hia.api.app, against a pre-populated event store.

The lifespan's HA client is pointed at an address nothing listens on for these — it
just sits reconnecting harmlessly in the background (the same reconnect-forever
behaviour verified against a real instance in P0/P1), and since it never actually
connects, the background ingest_and_relay loop never has anything to write —
meaning these tests never exercise the asyncio.to_thread DuckDB-write path, which
is deliberate (see test_state.py's docstring for why that path is tested in
isolation instead of through TestClient).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hia.api.app import create_app
from hia.config import Settings
from hia.ingest.store import EventStore
from tests.ingest.factories import make_state_changed_watched


def _settings(data_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=str(data_dir),
        ha_url="http://127.0.0.1:1",  # nothing listens here
        ha_token="unused",
        reconnect_initial_delay=0.01,
        reconnect_max_delay=0.02,
    )


def test_health(tmp_path: Path) -> None:
    EventStore(tmp_path / "hia.duckdb").close()
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_entities_returns_the_latest_state_per_entity(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hia.duckdb") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a", new_state="off"))
        store.write_watched_event(
            make_state_changed_watched(2, "light.a", new_state="on", old_state="off")
        )

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/entities")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["entity_id"] == "light.a"
    assert body[0]["state"] == "on"


def test_data_quality_reports_totals(tmp_path: Path) -> None:
    with EventStore(tmp_path / "hia.duckdb") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a"))

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/data-quality")

    assert response.status_code == 200
    body = response.json()
    assert body["total_state_changes"] == 1
    assert body["tracked_entities"] == 1


def test_serving_with_no_prior_data_starts_cleanly_with_an_empty_store(
    tmp_path: Path,
) -> None:
    """`hia serve` owns the store outright (hia.api.state's module docstring) —
    unlike the old design, it does not require a separate `hia ingest` to have run
    first; a fresh install with no history yet is a legitimate starting state, not
    an error. (tmp_path has no hia.duckdb in it.)"""
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/entities")
    assert response.status_code == 200
    assert response.json() == []
