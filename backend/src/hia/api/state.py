"""``hia serve``'s application state: the one DuckDB connection it owns, and the
live-relay connection manager.

**Why one process does both ingestion and serving.** Early P2 design split these
into separate processes — ``hia ingest`` writing, ``hia serve`` reading the same
file read-only — on the understanding that DuckDB supports a read-write process
coexisting with separate read-only readers. That understanding was wrong: verified
against a real Linux container (not just Windows, where it was first hit), DuckDB's
actual model is that a database file is opened in *either* read-write mode (exactly
one process, full stop) *or* read-only mode (any number of processes, none of which
may write) — never a mix of one writer and separate readers at the same time. See
https://duckdb.org/docs/stable/connect/concurrency and docs/HANDOFF.md.

So ``hia serve`` now owns the store outright: it ingests (writes) and serves reads
from the *same* connection, in the *same* process. ``hia ingest``, ``hia backfill``
and ``hia data-quality`` remain as standalone tools (useful headless, for debugging,
or for one-off backfills) but **must not run at the same time as `hia serve`**
against the same data directory — attempting to will surface DuckDB's own
"Conflicting lock is held" error, not a silent corruption.

A single DuckDB connection object is also not documented as safe for concurrent use
from multiple threads (only sequential use, or per-thread ``cursor()``s that still
serialize against each other) — so every access here, read or write, goes through
:meth:`run_db`, which holds one lock around one connection. A home dashboard's query
volume is nowhere near where that serialization would be a real bottleneck.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable
from typing import TypeVar

from hia.api.live import ConnectionManager, state_changed_message
from hia.config import Settings
from hia.ha.models import WatchedEvent
from hia.ingest.store import EventStore

T = TypeVar("T")


class AppState:
    def __init__(self, settings: Settings, store: EventStore) -> None:
        self.settings = settings
        self.store = store
        self.db_lock = asyncio.Lock()
        self.manager = ConnectionManager()

    async def run_db(self, fn: Callable[[EventStore], T]) -> T:
        async with self.db_lock:
            return await asyncio.to_thread(fn, self.store)

    async def ingest_and_relay(self, events: AsyncIterable[WatchedEvent]) -> None:
        """The one loop that drives everything ``hia serve`` does with live data:
        every subscribed event gets written durably; ``state_changed`` events with
        a real new state also get broadcast to connected browser clients. One pass
        over ``events`` — not one consumer for storage and a second, independent
        one for the relay — because :meth:`HomeAssistantClient.events
        <hia.ha.client.HomeAssistantClient.events>` is a single-consumer async
        generator; a second call would open a second, wasteful HA connection.
        """
        async for watched in events:

            def _write(store: EventStore, watched: WatchedEvent = watched) -> None:
                store.write_watched_event(watched)

            await self.run_db(_write)
            state_changed = watched.event.as_state_changed()
            if state_changed is not None and state_changed.new_state is not None:
                await self.manager.broadcast(
                    state_changed_message(
                        entity_id=state_changed.entity_id,
                        state=state_changed.new_state.state,
                        attributes=state_changed.new_state.attributes,
                        last_updated=state_changed.new_state.last_updated.isoformat(),
                    )
                )
