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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hia.api.actors import ActorRow, build_actors_payload
from hia.api.ingress import RestrictToSupervisorMiddleware
from hia.api.live import start_background_task, stop_background_task
from hia.api.state import AppState
from hia.config import Settings
from hia.ha.client import HomeAssistantClient
from hia.ingest.quality import QualityReport, build_report
from hia.ingest.store import EventStore, LatestState
from hia.logging import get_logger
from hia.provenance.actors import ActorClass

logger = get_logger(__name__)


class ConfirmActorRequest(BaseModel):
    """The POST body for confirming an actor's classification. Deliberately
    module-level, not a local class inside `create_app` (every other route
    handler here is nested there): reproduced directly on this dev machine —
    with `from __future__ import annotations` active, FastAPI cannot resolve a
    locally-scoped Pydantic model's string annotation (it isn't in the
    function's `__globals__`), silently treats the body param as a query
    param instead, and every request 422s with "Field required" on `body`."""

    actor_class: ActorClass


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_path = Path(settings.data_dir) / "hia.duckdb"
        store = EventStore(db_path)  # read-write: hia serve owns ingestion itself
        session = aiohttp.ClientSession()
        state = AppState(settings, store, session)
        app.state.hia = state

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

    @app.get("/api/provenance/actors")
    async def list_actors() -> list[ActorRow]:
        """Layer 3's setup task (docs/05-provenance.md §4): every HA user
        account, merged with how often it's shown up in this project's own
        history, a heuristic suggestion, and any classification the owner has
        already confirmed."""
        state: AppState = app.state.hia
        client = state.new_client()
        return await build_actors_payload(client, state.store)

    @app.post("/api/provenance/actors/{user_id}")
    async def confirm_actor(user_id: str, body: ConfirmActorRequest) -> dict[str, str]:
        """Records the owner's confirmation — the only place in this codebase
        that's allowed to call ``set_actor_classification`` at all (see
        hia.provenance.actors' module docstring: nothing computes one on its
        own). ``user_id`` isn't validated against the live HA user list here —
        confirming a stale/deleted account's classification is harmless and
        the id was presumably copied from a real ``GET`` response anyway."""
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id must not be empty")
        state: AppState = app.state.hia

        def _write(store: EventStore) -> None:
            store.set_actor_classification(user_id, body.actor_class)

        await state.run_db(_write)
        return {"status": "ok"}

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

    # Registered last: Starlette matches routes in registration order, so the
    # explicit /api/* operations above always take precedence over this catch-all.
    # Mounted only if the frontend has actually been built — a fresh checkout
    # before `npm run build`, or a backend-only dev session, should still run the
    # API rather than fail to start.
    frontend_dist = Path(settings.frontend_dist_dir)
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        logger.info("frontend_not_built", looked_in=str(frontend_dist))

    return app
