"""``hia`` command-line entry point.

P0's exit criterion lives here: ``uv run hia watch`` streams live state changes from
a real Home Assistant instance and survives a Core restart without losing the
subscription (docs/04-roadmap.md, P0). P1's builds on it: ``hia ingest`` runs the
same reconnect-proof stream, but durably, into the event store.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import typer

from hia.config import Settings, get_settings
from hia.ha.client import HomeAssistantClient
from hia.ha.registry import Registries, fetch_all
from hia.ingest.pipeline import run_ingest
from hia.ingest.quality import build_report, format_report
from hia.ingest.store import EventStore
from hia.logging import configure_logging, get_logger

app = typer.Typer(add_completion=False, help="Home Intelligent Assistant backend CLI.")
logger = get_logger(__name__)


def _client_for(settings: Settings, session: aiohttp.ClientSession) -> HomeAssistantClient:
    return HomeAssistantClient(
        settings.ha_url,
        settings.ha_token,
        session=session,
        initial_delay=settings.reconnect_initial_delay,
        max_delay=settings.reconnect_max_delay,
        backoff_factor=settings.reconnect_backoff_factor,
        queue_max_size=settings.event_queue_max_size,
    )


@app.command()
def watch(
    event_type: list[str] = typer.Option(
        ["state_changed"],
        "--event-type",
        "-e",
        help="Event type(s) to subscribe to. Repeat to subscribe to more than one.",
    ),
) -> None:
    """Stream live events from Home Assistant to stdout until interrupted."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    asyncio.run(_watch(settings, event_type))


async def _watch(settings: Settings, event_types: list[str]) -> None:
    async with aiohttp.ClientSession() as session:
        client = _client_for(settings, session)
        async for watched in client.events(event_types):
            state_changed = watched.event.as_state_changed()
            new_state = (
                state_changed.new_state.state
                if state_changed and state_changed.new_state
                else None
            )
            logger.info(
                "event",
                seq=watched.seq,
                resumed_after_gap=watched.resumed_after_gap,
                event_type=watched.event.event_type,
                entity_id=watched.event.data.get("entity_id"),
                new_state=new_state,
            )


@app.command()
def ingest(
    event_type: list[str] | None = typer.Option(
        None,
        "--event-type",
        "-e",
        help="Event type(s) to subscribe to. Defaults to HIA_INGEST_EVENT_TYPES "
        "(state_changed, automation_triggered, script_started, call_service).",
    ),
    stop_after: int = typer.Option(
        0, "--stop-after", help="Exit after writing this many events. 0 = run forever."
    ),
) -> None:
    """Stream live events into the durable event store. This is `watch`, but
    persisted — P1's exit criterion (docs/04-roadmap.md)."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    event_types = event_type or settings.ingest_event_types
    asyncio.run(_ingest(settings, event_types, stop_after or None))


async def _ingest(settings: Settings, event_types: list[str], stop_after: int | None) -> None:
    db_path = Path(settings.data_dir) / "hia.duckdb"
    with EventStore(db_path) as store:
        logger.info("ingest_starting", db_path=str(db_path), event_types=event_types)
        async with aiohttp.ClientSession() as session:
            client = _client_for(settings, session)
            await run_ingest(client, store, event_types, stop_after=stop_after)


@app.command(name="data-quality")
def data_quality() -> None:
    """Print a report on the event store: entity coverage, staleness, and how many
    times the live stream reconnected (docs/02-architecture.md `ingest/`)."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    db_path = Path(settings.data_dir) / "hia.duckdb"
    with EventStore(db_path) as store:
        print(format_report(build_report(store)))


@app.command()
def registry() -> None:
    """Dump the entity/device/area/floor/label registries as JSON."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    asyncio.run(_registry(settings))


async def _registry(settings: Settings) -> None:
    async with aiohttp.ClientSession() as session:
        client = _client_for(settings, session)
        registries = await fetch_all(client)
        print(json.dumps(_registries_to_dict(registries), indent=2, default=str))


def _registries_to_dict(registries: Registries) -> dict[str, list[dict[str, object]]]:
    return {
        "entities": [e.model_dump(mode="json") for e in registries.entities],
        "devices": [d.model_dump(mode="json") for d in registries.devices],
        "areas": [a.model_dump(mode="json") for a in registries.areas],
        "floors": [f.model_dump(mode="json") for f in registries.floors],
        "labels": [label.model_dump(mode="json") for label in registries.labels],
    }


@app.command()
def check() -> None:
    """Connect, authenticate, and disconnect — a quick smoke test of the configured
    HIA_HA_URL / HIA_HA_TOKEN."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    asyncio.run(_check(settings))


async def _check(settings: Settings) -> None:
    async with aiohttp.ClientSession() as session:
        client = _client_for(settings, session)
        await client.call({"type": "config/area_registry/list"})
        logger.info("check_ok", url=settings.ha_url)


if __name__ == "__main__":
    app()
