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
from datetime import UTC, datetime, timedelta
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

-- Layer 3 (docs/05-provenance.md §4, hia.provenance.actors): every observed
-- context_user_id maps to at most one owner-confirmed classification. Only
-- ever holds *confirmed* entries -- the docs are explicit that a heuristic
-- suggestion (system_generated flag, name matching, temporal regularity) is
-- never itself a classification, so suggestions are computed on demand and
-- never written here.
CREATE TABLE IF NOT EXISTS actor_classifications (
    user_id VARCHAR PRIMARY KEY,
    actor_class VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    classified_at TIMESTAMPTZ NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class EntityActivity:
    """One row of the data-quality report: per-entity ingest summary."""

    entity_id: str
    row_count: int
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class ProvenanceEvent:
    """One row from the ``events`` table, narrowed to the three types the
    provenance classifier (P3, hia.provenance) is built from:
    ``automation_triggered``, ``script_started``, ``call_service``. Everything
    Layer 1's context-chain resolver and Layer 2's automation-fire correlator need
    is here. ``context_user_id`` is Layer 3's input (hia.provenance.actors): a
    ``call_service`` or ``script_started`` event fired directly by a person (via
    the app, a script "run now" click) carries that person's own user_id on
    *this* event, which is what distinguishes it from one HA's automation engine
    fired on its own — those carry no user_id at all."""

    event_type: str
    time_fired: datetime
    context_id: str
    context_parent_id: str | None
    context_user_id: str | None
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class StateChangeForProvenance:
    """The columns of a ``state_changes`` row the provenance classifier reads —
    everything else about the row (state value, attributes, ...) is irrelevant to
    working out who caused it."""

    entity_id: str
    changed_at: datetime | None
    context_id: str | None
    context_parent_id: str | None


@dataclass(frozen=True, slots=True)
class LatestState:
    """The most recently known state of one entity — what a "live entity view"
    renders (hia.api), read straight from the store rather than requiring its own
    live HA connection."""

    entity_id: str
    state: str | None
    attributes: dict[str, object] | None
    last_changed: datetime | None
    last_updated: datetime | None


@dataclass(frozen=True, slots=True)
class HourlyActivity:
    """One bar of the frontend's activity chart: how many ``state_changed``
    rows landed in this hour-aligned bucket (UTC)."""

    hour: datetime
    count: int


class EventStore:
    """Owns one DuckDB connection and the schema on it.

    DuckDB's actual multi-process concurrency model — confirmed against a real
    Linux container, after an initial (wrong) reading of its docs assumed
    otherwise, see docs/HANDOFF.md — is exactly one of two regimes for a given
    file: **read-write** (exactly one process, full stop) or **read-only** (any
    number of processes, none of which may write). There is no mode where one
    writer coexists with separate readers. `hia serve` therefore owns the store
    outright — it ingests *and* serves reads through the same connection, in the
    same process — rather than reading a store a separate `hia ingest` process
    writes to, the way early P2 design assumed. `hia ingest`/`hia backfill`/
    `hia data-quality` remain as standalone tools, but none of them may run at the
    same time as `hia serve` (or each other) against the same data directory —
    attempting to will surface DuckDB's own "Conflicting lock is held" error.

    A single connection object is also not documented as safe for concurrent use
    from multiple threads (`hia.api.state.AppState` is what serializes access to
    it with a lock, for the one process — `hia serve` — that needs to)."""

    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None:
        path = Path(db_path)
        if read_only:
            if not path.exists():
                raise FileNotFoundError(
                    f"Event store not found: {path}. Has `hia ingest` been run yet?"
                )
            self._con = duckdb.connect(str(path), read_only=True)
        else:
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

    def latest_states(self) -> list[LatestState]:
        """The most recent row per entity — the "live entity view" (hia.api).

        Ties on ``last_updated`` (possible with same-timestamp backfill rows, and
        common in tests using a fixed clock) break on ``id`` — insertion order —
        rather than arbitrarily, so this is deterministic even when HA's own
        timestamp doesn't distinguish two rows.
        """
        rows = self._con.execute(
            """
            SELECT entity_id, state, attributes, last_changed, last_updated
            FROM state_changes
            QUALIFY row_number()
                OVER (PARTITION BY entity_id ORDER BY last_updated DESC NULLS LAST, id DESC) = 1
            ORDER BY entity_id
            """
        ).fetchall()
        return [
            LatestState(
                entity_id=r[0],
                state=r[1],
                attributes=json.loads(r[2]) if r[2] is not None else None,
                last_changed=r[3],
                last_updated=r[4],
            )
            for r in rows
        ]

    def state_change_counts_by_hour(self, *, hours: int = 24) -> list[HourlyActivity]:
        """State-change counts bucketed by hour, oldest first, covering the
        trailing ``hours`` window (default 24) up to and including the current,
        still-in-progress hour — the frontend's activity chart. Every bucket in
        the window is present, including ones with zero rows: a quiet hour is
        real information (nothing happened), not a gap the caller has to notice
        and fill in itself."""
        current_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        window_start = current_hour - timedelta(hours=hours - 1)

        rows = self._con.execute(
            """
            SELECT date_trunc('hour', last_updated) AS hour, count(*)
            FROM state_changes
            WHERE last_updated >= ?
            GROUP BY 1
            """,
            [window_start],
        ).fetchall()
        counts: dict[datetime, int] = dict(rows)

        return [
            HourlyActivity(
                hour=window_start + timedelta(hours=i),
                count=counts.get(window_start + timedelta(hours=i), 0),
            )
            for i in range(hours)
        ]

    def events_for_provenance(self) -> list[ProvenanceEvent]:
        """``automation_triggered``/``script_started``/``call_service`` rows,
        oldest first — the raw material ``hia.provenance``'s Layer 1 context-chain
        resolver and Layer 2 automation-fire correlator are built from."""
        rows = self._con.execute(
            """
            SELECT event_type, time_fired, context_id, context_parent_id,
                   context_user_id, data
            FROM events
            WHERE event_type IN ('automation_triggered', 'script_started', 'call_service')
            ORDER BY time_fired
            """
        ).fetchall()
        return [
            ProvenanceEvent(
                event_type=r[0],
                time_fired=r[1],
                context_id=r[2],
                context_parent_id=r[3],
                context_user_id=r[4],
                data=json.loads(r[5]),
            )
            for r in rows
        ]

    def state_changes_for_provenance(self) -> list[StateChangeForProvenance]:
        """Every ``state_changes`` row, oldest first, narrowed to what
        ``hia.provenance`` needs to classify it."""
        rows = self._con.execute(
            """
            SELECT entity_id, last_updated, context_id, context_parent_id
            FROM state_changes
            ORDER BY last_updated
            """
        ).fetchall()
        return [
            StateChangeForProvenance(
                entity_id=r[0], changed_at=r[1], context_id=r[2], context_parent_id=r[3]
            )
            for r in rows
        ]

    def set_actor_classification(
        self, user_id: str, actor_class: str, *, source: str = "manual"
    ) -> None:
        """Records an owner-confirmed Layer 3 classification (hia.provenance.actors)
        for one HA user. **Only ever call this with an owner's actual confirmation**
        — see hia.provenance.actors' module docstring for why nothing in this
        codebase computes one on its own. Upserts: re-confirming an actor replaces
        the prior classification (someone's role can change) rather than erroring."""
        self._con.execute(
            """
            INSERT INTO actor_classifications (user_id, actor_class, source, classified_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                actor_class = excluded.actor_class,
                source = excluded.source,
                classified_at = excluded.classified_at
            """,
            [user_id, actor_class, source, datetime.now(UTC)],
        )

    def actor_classifications(self) -> dict[str, str]:
        """``user_id -> actor_class`` for every owner-confirmed actor."""
        rows = self._con.execute(
            "SELECT user_id, actor_class FROM actor_classifications"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def user_event_timestamps(self) -> dict[str, list[datetime]]:
        """Every observed ``context_user_id`` (across both tables) mapped to its
        event timestamps — the raw material for hia.provenance.actors'
        temporal-regularity suggestion heuristic. An actor who has never caused an
        event neither table has a row for isn't in the result; that's correct,
        there's nothing to compute regularity from."""
        rows = self._con.execute(
            """
            SELECT context_user_id, last_updated FROM state_changes
                WHERE context_user_id IS NOT NULL
            UNION ALL
            SELECT context_user_id, time_fired FROM events
                WHERE context_user_id IS NOT NULL
            ORDER BY 2
            """
        ).fetchall()
        result: dict[str, list[datetime]] = {}
        for user_id, ts in rows:
            result.setdefault(user_id, []).append(ts)
        return result

    def gap_resumption_count(self) -> int:
        """How many stored rows were the first event delivered after a client
        reconnect (``resumed_after_gap``) — a proxy for how many times, and how
        often, the live window may have missed events."""
        return self._scalar(
            "SELECT count(*) FROM state_changes WHERE resumed_after_gap"
        ) + self._scalar("SELECT count(*) FROM events WHERE resumed_after_gap")
