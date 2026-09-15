"""The live ingest loop: consumes :meth:`HomeAssistantClient.events` and persists
every event to the :class:`~hia.ingest.store.EventStore`.

This is what P0's exit criterion — a client that survives a Core restart — is *for*.
P1's exit criterion builds directly on it: the same 72-hour-without-a-gap run, but
now every event lands durably instead of just being logged to stdout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from hia.ha.client import HomeAssistantClient
from hia.ingest.store import EventStore
from hia.logging import get_logger

logger = get_logger(__name__)

_STATS_LOG_INTERVAL = 500
"""Log a running total every this many events, rather than per-event at info level
— per-event logging is what `hia watch` is for; ingest just needs to prove it's
alive and roughly how much it has stored."""


async def run_ingest(
    client: HomeAssistantClient,
    store: EventStore,
    event_types: Iterable[str],
    *,
    stop_after: int | None = None,
) -> None:
    """Run forever (or until ``stop_after`` events have been written — used by
    tests and by a future "ingest N events and exit" smoke-test mode).

    Each write runs in a thread (DuckDB's Python API is synchronous) so a write
    never blocks this coroutine from doing anything else sharing the event loop —
    consistent with why the client's own reader is decoupled from consumer speed
    (docs/02-architecture.md's backpressure note).
    """
    written = 0
    async for watched in client.events(event_types):
        await asyncio.to_thread(store.write_watched_event, watched)
        written += 1
        if written % _STATS_LOG_INTERVAL == 0:
            state_changes, events = await asyncio.gather(
                asyncio.to_thread(store.state_change_count),
                asyncio.to_thread(store.event_count),
            )
            logger.info(
                "ingest_progress",
                written=written,
                state_changes=state_changes,
                events=events,
                dropped=client.dropped_event_count,
            )
        if stop_after is not None and written >= stop_after:
            return
