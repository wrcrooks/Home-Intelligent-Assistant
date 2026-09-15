from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from hia.ha.models import WatchedEvent
from hia.ingest.pipeline import run_ingest
from hia.ingest.store import EventStore
from tests.ingest.factories import make_state_changed_watched


class _FakeClient:
    """Just enough of HomeAssistantClient's surface for run_ingest: an async
    generator of events, and the dropped-event counter it logs alongside progress."""

    def __init__(self, events: list[WatchedEvent]) -> None:
        self._events = events
        self.dropped_event_count = 0

    async def events(self, event_types: Iterable[str]) -> AsyncIterator[WatchedEvent]:
        for event in self._events:
            yield event


async def test_run_ingest_writes_every_event() -> None:
    events = [make_state_changed_watched(i, f"light.{i}") for i in range(1, 6)]
    client = _FakeClient(events)
    with EventStore(":memory:") as store:
        await run_ingest(client, store, ["state_changed"])
        assert store.state_change_count() == 5


async def test_run_ingest_stops_after_the_requested_count() -> None:
    events = [make_state_changed_watched(i, f"light.{i}") for i in range(1, 6)]
    client = _FakeClient(events)
    with EventStore(":memory:") as store:
        await run_ingest(client, store, ["state_changed"], stop_after=3)
        assert store.state_change_count() == 3
