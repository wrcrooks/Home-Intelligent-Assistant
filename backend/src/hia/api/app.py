"""The FastAPI app: ``hia serve``, the process that actually ships in the add-on.

Owns the event store outright — connects to Home Assistant, ingests every
subscribed event durably, relays ``state_changed`` events live to connected browser
clients, and serves REST reads over the same store, all through one DuckDB
connection. See hia.api.state's module docstring for why it's one process rather
than a separate reader alongside ``hia ingest``: DuckDB does not support that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from hia.api.ingress import RestrictToSupervisorMiddleware
from hia.api.live import start_background_task, stop_background_task
from hia.api.state import AppState
from hia.config import Settings
from hia.ha.client import HomeAssistantClient
from hia.ingest.quality import QualityReport, build_report
from hia.ingest.store import EventStore, LatestState
from hia.logging import get_logger

logger = get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_path = Path(settings.data_dir) / "hia.duckdb"
        store = EventStore(db_path)  # read-write: hia serve owns ingestion itself
        state = AppState(settings, store)
        app.state.hia = state

        session = aiohttp.ClientSession()
        client = HomeAssistantClient(
            settings.ha_url,
            settings.ha_token,
            session=session,
            initial_delay=settings.reconnect_initial_delay,
            max_delay=settings.reconnect_max_delay,
            backoff_factor=settings.reconnect_backoff_factor,
            queue_max_size=settings.event_queue_max_size,
        )
        task = start_background_task(
            state.ingest_and_relay(client.events(settings.ingest_event_types)),
            name="ingest_and_relay",
        )
        logger.info("api_started", db_path=str(db_path), addon=settings.is_addon)

        try:
            yield
        finally:
            await stop_background_task(task)
            await session.close()
            store.close()

    app = FastAPI(title="Home Intelligent Assistant", lifespan=lifespan)
    app.add_middleware(RestrictToSupervisorMiddleware, enabled=settings.is_addon)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/entities")
    async def entities() -> list[LatestState]:
        state: AppState = app.state.hia
        return await state.run_db(lambda store: store.latest_states())

    @app.get("/api/data-quality")
    async def data_quality() -> QualityReport:
        state: AppState = app.state.hia
        return await state.run_db(build_report)

    @app.websocket("/api/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        state: AppState = app.state.hia
        await state.manager.connect(websocket)
        try:
            # This endpoint is broadcast-only; block on receive purely to detect
            # the client disconnecting (any message, or none, ends the loop).
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.manager.disconnect(websocket)

    return app
