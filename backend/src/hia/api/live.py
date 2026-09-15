"""The live WebSocket relay: fans out state_changed events to connected browser
clients in real time.

The actual event source is :meth:`AppState.ingest_and_relay
<hia.api.state.AppState.ingest_and_relay>`, not a separate connection of its own —
see hia.ingest.store's and hia.api.state's module docstrings for why ``hia serve``
owns ingestion outright rather than reading a store a separate ``hia ingest``
process writes to. This module only holds the browser-facing fan-out.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable

from fastapi import WebSocket

from hia.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """Tracks connected browser clients and fans out messages to all of them. A
    client that errors on send (closed connection the server hasn't noticed yet) is
    dropped rather than taking the whole broadcast down."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, message: dict[str, object]) -> None:
        dead: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception:  # noqa: BLE001 — a dead client must never break the rest
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)


def start_background_task(coro: Awaitable[None], *, name: str) -> asyncio.Task[None]:
    """Runs ``coro`` as a background task for the app's lifetime, logging (rather
    than silently swallowing) a crash instead of letting it vanish. The caller (the
    app's lifespan) is responsible for cancelling the returned task on shutdown via
    :func:`stop_background_task`."""

    async def _guarded() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background_task_crashed", task=name)
            raise

    return asyncio.create_task(_guarded(), name=name)


async def stop_background_task(task: asyncio.Task[None]) -> None:
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def state_changed_message(
    entity_id: str, state: str, attributes: dict[str, object], last_updated: str
) -> dict[str, object]:
    """The small, browser-friendly projection broadcast over the WebSocket — not
    the full internal WatchedEvent/HAEvent shape."""
    return {
        "type": "state_changed",
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes,
        "last_updated": last_updated,
    }
