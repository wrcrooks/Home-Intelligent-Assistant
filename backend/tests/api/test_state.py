"""AppState.ingest_and_relay in isolation: a fake event stream in, real DuckDB
writes and real ConnectionManager broadcasts out — no TestClient, no real network.

Why not exercise this through the real `/api/ws/events` route end-to-end: doing so
means a real `asyncio.to_thread` DuckDB write firing from a background task on
Starlette TestClient's own portal thread, concurrently with the test's outer
coroutine blocked in TestClient's cross-thread receive bridge — a combination that
reproduces a `Windows fatal exception: access violation` deep in CPython's
asyncio/selectors internals on this project's Windows dev environment (reproduced
under both the default ProactorEventLoop and, after switching to it specifically to
rule that out, SelectorEventLoop too — see tests/conftest.py). Not a bug in this
project's code: the same logic, tested here without that thread/loop combination,
and separately verified end-to-end against a real Home Assistant instance
(docs/HANDOFF.md), works correctly. The add-on itself only ever runs on Linux.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp

from hia.api.state import AppState
from hia.config import Settings
from hia.ha.models import WatchedEvent
from hia.ingest.store import EventStore
from tests.ingest.factories import make_generic_watched, make_state_changed_watched


class _FakeWebSocket:
    """Just enough of Starlette's WebSocket surface for ConnectionManager."""

    def __init__(self) -> None:
        self.received: list[dict[str, object]] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, message: dict[str, object]) -> None:
        self.received.append(message)


async def _events(*watched: WatchedEvent) -> AsyncIterator[WatchedEvent]:
    for w in watched:
        yield w


async def test_ingest_and_relay_writes_every_event_and_broadcasts_state_changes() -> None:
    with EventStore(":memory:") as store:
        async with aiohttp.ClientSession() as session:
            state = AppState(Settings(_env_file=None), store, session)
            fake_ws = _FakeWebSocket()
            await state.manager.connect(fake_ws)  # type: ignore[arg-type]

            await state.ingest_and_relay(
                _events(
                    make_state_changed_watched(1, "light.a", new_state="on"),
                    make_generic_watched(2, "call_service", {"domain": "light"}),
                )
            )

            assert store.state_change_count() == 1
            assert store.event_count() == 1
            assert len(fake_ws.received) == 1
            assert fake_ws.received[0]["entity_id"] == "light.a"
            assert fake_ws.received[0]["state"] == "on"


async def test_ingest_and_relay_stores_but_does_not_broadcast_a_removed_entity() -> None:
    """new_state=None (the entity was removed) has nothing meaningful to show in a
    live view, but is still real history worth keeping."""
    with EventStore(":memory:") as store:
        async with aiohttp.ClientSession() as session:
            state = AppState(Settings(_env_file=None), store, session)
            fake_ws = _FakeWebSocket()
            await state.manager.connect(fake_ws)  # type: ignore[arg-type]

            await state.ingest_and_relay(_events(make_state_changed_watched(1, "light.a", new_state=None)))

            assert store.state_change_count() == 1
            assert fake_ws.received == []


async def test_ingest_and_relay_with_no_connected_clients_still_writes() -> None:
    with EventStore(":memory:") as store:
        async with aiohttp.ClientSession() as session:
            state = AppState(Settings(_env_file=None), store, session)
            await state.ingest_and_relay(_events(make_state_changed_watched(1, "light.a", new_state="on")))
            assert store.state_change_count() == 1
