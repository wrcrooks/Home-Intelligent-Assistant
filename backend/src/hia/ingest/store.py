"""The event store: a DuckDB database holding every event this project has ever
seen, live or backfilled.

Two tables, matching the two shapes of thing worth keeping (docs/02-architecture.md
`ingest/`):

``state_changes``
    One typed row per ``state_changed`` event — the dominant, heavily-queried event
    type, so it gets real columns (``entity_id``, ``state``, ``attributes``, ...)
    rather than living buried in a JSON blob. Populated identically by the live
    writer and (from a later slice of P1) recorder backfill, with a ``source``
    column distinguishing them — "live and historical data are indistinguishable
    downstream" (docs/02-architecture.md).

``events``
    Everything else this project subscribes to (``automation_triggered``,
    ``script_started``, ``call_service``, and any future event type) — captured now,
    interpreted later by the provenance classifier (P3). Kept generic (a JSON
    ``data`` column) because nothing downstream needs typed access to these yet, and
    guessing at a schema before P3 exists would just mean migrating it later.

Both tables carry ``context_id``/``context_parent_id``/``context_user_id`` on every
row from day one — captured now because it cannot be reconstructed later
(docs/05-provenance.md §7, docs/HANDOFF.md "three things that cannot be retrofitted").

DuckDB is synchronous; every write here blocks the calling thread. The ingest
pipeline (``hia.ingest.pipeline``) is what keeps that off the asyncio event loop —
this module makes no assumptions about asyncio at all, deliberately, so it stays
trivial to unit test and reusable from the (synchronous) recorder backfill.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb

from hia.ha.models import HAEvent, WatchedEvent

Source = Literal["live", "backfill"]

_SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS state_changes_id_seq;
CREATE TABLE IF NOT EXISTS state_changes (
    id BIGINT PRIMARY KEY DEFAULT nextval('state_changes_id_seq'),
    entity_id VARCHAR NOT NULL,
    state VARCHAR,
    attributes VARCHAR,
    old_state VARCHAR,
    last_changed TIMESTAMPTZ,
    last_updated TIMESTAMPTZ,
    context_id VARCHAR,
    context_parent_id VARCHAR,
    context_user_id VARCHAR,
    source VARCHAR NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    client_seq BIGINT,
    resumed_after_gap BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_state_changes_entity_time
    ON state_changes (entity_id, last_updated);

CREATE SEQUENCE IF NOT EXISTS events_id_seq;
CREATE TABLE IF NOT EXISTS events (
    id BIGINT PRIMARY KEY DEFAULT nextval('events_id_seq'),
    event_type VARCHAR NOT NULL,
    time_fired TIMESTAMPTZ NOT NULL,
    context_id VARCHAR NOT NULL,
    context_parent_id VARCHAR,
    context_user_id VARCHAR,
    data VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    client_seq BIGINT,
    resumed_after_gap BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events (event_type, time_fired);
"""


@dataclass(frozen=True, slots=True)
class EntityActivity:
    """One row of the data-quality report: per-entity ingest summary."""

    entity_id: str
    row_count: int
    first_seen: datetime
    last_seen: datetime


class EventStore:
    """Owns one DuckDB connection and the schema on it.

    DuckDB allows one read-write connection to a database file at a time — this
    class is meant to be held for the lifetime of the ingest process (or a backfill
    run), not opened per-write. A future web API reading from the same file while
    ingest is running (P2) will need its own story here — either DuckDB's read-only
    connection mode, if the installed version supports concurrent readers alongside
    a writer, or querying through this process rather than the file directly. Not
    solved yet; flagged so it doesn't get silently forgotten.
    """

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(_SCHEMA)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def write_watched_event(self, watched: WatchedEvent, *, source: Source = "live") -> None:
        """Route a live event to the right table. The single entry point the ingest
        pipeline calls; callers never need to know about ``state_changes`` vs.
        ``events`` directly."""
        event = watched.event
        state_changed = event.as_state_changed()
        if state_changed is not None:
            new, old = state_changed.new_state, state_changed.old_state
            self.write_state_change(
                entity_id=state_changed.entity_id,
                state=new.state if new else None,
                attributes=new.attributes if new else None,
                old_state=old.state if old else None,
                last_changed=new.last_changed if new else None,
                last_updated=new.last_updated if new else None,
                context_id=event.context.id,
                context_parent_id=event.context.parent_id,
                context_user_id=event.context.user_id,
                source=source,
                client_seq=watched.seq,
                resumed_after_gap=watched.resumed_after_gap,
            )
        else:
            self.write_event(
                event,
                source=source,
                client_seq=watched.seq,
                resumed_after_gap=watched.resumed_after_gap,
            )

    def write_state_change(
        self,
        *,
        # context_id is nullable here (unlike `events`, which is live-only and
        # always has one): a small number of very old recorder rows predate context
        # tracking entirely, and backfill would rather store what it has than drop
        # the row or fabricate a value.
        entity_id: str,
        state: str | None,
        attributes: dict[str, object] | None,
        old_state: str | None,
        last_changed: datetime | None,
        last_updated: datetime | None,
        context_id: str | None,
        context_parent_id: str | None,
        context_user_id: str | None,
        source: Source,
        client_seq: int | None = None,
        resumed_after_gap: bool | None = None,
    ) -> None:
        self._con.execute(
            """
            INSERT INTO state_changes (
                entity_id, state, attributes, old_state, last_changed, last_updated,
                context_id, context_parent_id, context_user_id,
                source, ingested_at, client_seq, resumed_after_gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entity_id,
                state,
                json.dumps(attributes) if attributes is not None else None,
                old_state,
                last_changed,
                last_updated,
                context_id,
                context_parent_id,
                context_user_id,
                source,
                datetime.now(UTC),
                client_seq,
                resumed_after_gap,
            ],
        )

    def write_event(
        self,
        event: HAEvent,
        *,
        source: Source,
        client_seq: int | None = None,
        resumed_after_gap: bool | None = None,
    ) -> None:
        self._con.execute(
            """
            INSERT INTO events (
                event_type, time_fired, context_id, context_parent_id, context_user_id,
                data, source, ingested_at, client_seq, resumed_after_gap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event.event_type,
                event.time_fired,
                event.context.id,
                event.context.parent_id,
                event.context.user_id,
                json.dumps(event.data),
                source,
                datetime.now(UTC),
                client_seq,
                resumed_after_gap,
            ],
        )

    def _scalar(self, sql: str) -> int:
        row = self._con.execute(sql).fetchone()
        assert row is not None  # a scalar aggregate (count(*), ...) always returns one row
        return int(row[0])

    def state_change_count(self) -> int:
        return self._scalar("SELECT count(*) FROM state_changes")

    def event_count(self) -> int:
        return self._scalar("SELECT count(*) FROM events")

    def entity_activity(self) -> list[EntityActivity]:
        """Per-entity row counts and first/last-seen timestamps — the basis of the
        data-quality report (``hia.ingest.quality``)."""
        rows = self._con.execute(
            """
            SELECT entity_id, count(*), min(ingested_at), max(ingested_at)
            FROM state_changes
            GROUP BY entity_id
            ORDER BY entity_id
            """
        ).fetchall()
        return [
            EntityActivity(entity_id=r[0], row_count=r[1], first_seen=r[2], last_seen=r[3])
            for r in rows
        ]

    def gap_resumption_count(self) -> int:
        """How many stored rows were the first event delivered after a client
        reconnect (``resumed_after_gap``) — a proxy for how many times, and how
        often, the live window may have missed events."""
        return self._scalar(
            "SELECT count(*) FROM state_changes WHERE resumed_after_gap"
        ) + self._scalar("SELECT count(*) FROM events WHERE resumed_after_gap")
