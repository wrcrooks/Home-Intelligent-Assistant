"""Recorder backfill: reads Home Assistant's own recorder database — read-only,
always — and writes state history into the *same* ``state_changes`` table the live
ingest pipeline writes to, tagged ``source="backfill"``, so live and historical data
are indistinguishable downstream (docs/02-architecture.md `ingest/`).

**SQLite only, and only that, has been verified against a real instance.** The
reader is built on SQLAlchemy specifically so MariaDB/Postgres support is "swap the
connection URL and add a driver dependency" rather than a rewrite — but neither has
been tried against a live database. Treat that path as unverified, not supported,
until someone actually runs it (docs/HANDOFF.md).

Schema handling: the recorder schema has moved a lot — ``states_meta`` normalisation,
epoch-float ``*_ts`` timestamp columns replacing datetime columns, binary
ULID/UUID-encoded context columns replacing string ones. Rather than trust a
hardcoded "schema_version >= N" cutoff (getting N exactly right is its own research
problem, and being wrong about it fails silently), this reader checks that the
specific columns it needs actually exist, via SQLAlchemy reflection, and raises a
clear, actionable error naming the schema_version and the missing columns if they
don't. Abstain rather than guess (CLAUDE.md) — a wrong read here would corrupt
training data silently, which is worse than refusing to read it at all.

Context binary columns are decoded with ``ulid-transform`` — the same PyPI package
Home Assistant's own recorder uses for this — rather than a hand-rolled
reimplementation, specifically so a backfilled ``context_id`` is byte-for-byte the
same string a live-captured row for the same real event would have had. Without
that, provenance's context-chain matching (docs/05-provenance.md §3) would silently
fail to join backfilled history to anything.

**Not handled yet, on purpose:** the ``statistics``/``statistics_short_term`` tables
(long-term rollups that outlive purged raw state history) are a real gap — flagged in
docs/HANDOFF.md, not silently dropped. Nor is de-duplication against rows a live
ingest run may already have captured for the same time window: backfill is for the
history *before* live ingestion started, and running it over an already-live-captured
range will currently produce duplicate rows (no uniqueness constraint exists yet to
prevent it) — documented, not solved, here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from ulid_transform import bytes_to_ulid_or_none

from hia.ingest.store import EventStore
from hia.logging import get_logger

logger = get_logger(__name__)

_PROGRESS_LOG_INTERVAL = 5000

REQUIRED_STATES_COLUMNS = frozenset(
    {
        "state_id",
        "state",
        "last_changed_ts",
        "last_updated_ts",
        "metadata_id",
        "old_state_id",
        "attributes_id",
        "context_id_bin",
        "context_user_id_bin",
        "context_parent_id_bin",
    }
)

_QUERY = sa.text(
    """
    SELECT
        sm.entity_id,
        s.state,
        s.last_changed_ts,
        s.last_updated_ts,
        s.context_id_bin,
        s.context_parent_id_bin,
        s.context_user_id_bin,
        old.state AS old_state,
        attrs.shared_attrs AS attributes_json
    FROM states s
    JOIN states_meta sm ON sm.metadata_id = s.metadata_id
    LEFT JOIN states old ON old.state_id = s.old_state_id
    LEFT JOIN state_attributes attrs ON attrs.attributes_id = s.attributes_id
    WHERE (:since_ts IS NULL OR s.last_updated_ts >= :since_ts)
      AND (:until_ts IS NULL OR s.last_updated_ts < :until_ts)
    ORDER BY s.last_updated_ts
    """
)


class UnsupportedRecorderSchemaError(RuntimeError):
    """Raised instead of attempting to read a recorder schema this reader hasn't
    been verified against."""


@dataclass(frozen=True, slots=True)
class BackfilledStateChange:
    """One recorder row, already converted to the shapes
    :meth:`EventStore.write_state_change` expects — typed so the conversion in
    :func:`read_state_changes` is the only place that touches raw row objects."""

    entity_id: str
    state: str | None
    attributes: dict[str, object] | None
    old_state: str | None
    last_changed: datetime | None
    last_updated: datetime | None
    context_id: str | None
    context_parent_id: str | None
    context_user_id: str | None


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    rows_read: int
    rows_written: int
    schema_version: int
    earliest: datetime | None
    latest: datetime | None


def sqlite_read_only_url(db_path: str | Path) -> str:
    """A SQLAlchemy URL that opens the SQLite file strictly read-only, and refuses
    to create it if missing — a typo'd path should error loudly, not produce a
    fresh empty database that looks like "no history" (docs/CLAUDE.md: the recorder
    DB is read-only, always)."""
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Recorder database not found: {path}")
    return f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true"


def _schema_version(engine: sa.Engine) -> int:
    try:
        with engine.connect() as con:
            version = con.execute(
                sa.text("SELECT MAX(schema_version) FROM schema_changes")
            ).scalar()
    except sa.exc.DBAPIError as exc:
        raise UnsupportedRecorderSchemaError(
            "Could not read schema_changes.schema_version — is this actually a "
            "Home Assistant recorder database?"
        ) from exc
    if version is None:
        raise UnsupportedRecorderSchemaError(
            "schema_changes has no rows — is this actually a Home Assistant "
            "recorder database?"
        )
    return int(version)


def _check_required_columns(engine: sa.Engine, schema_version: int) -> None:
    columns = {c["name"] for c in sa.inspect(engine).get_columns("states")}
    missing = REQUIRED_STATES_COLUMNS - columns
    if missing:
        raise UnsupportedRecorderSchemaError(
            f"Recorder schema_version={schema_version} is missing states column(s) "
            f"{sorted(missing)} that this reader needs (metadata_id and the "
            "context_*_bin columns are the usual culprits on a very old schema). "
            "Upgrade Home Assistant Core to migrate the database, or extend this "
            "reader for the older layout — see hia.ingest.backfill's module "
            "docstring."
        )


def _to_utc(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=UTC) if ts is not None else None


def read_state_changes(
    engine: sa.Engine,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    batch_size: int = 5000,
) -> Iterator[BackfilledStateChange]:
    """Stream rows out of the recorder in ``last_updated`` order. Streamed (not
    materialised as one giant list) so a large recorder database doesn't have to fit
    in memory at once."""
    params = {
        "since_ts": since.timestamp() if since else None,
        "until_ts": until.timestamp() if until else None,
    }
    with engine.connect().execution_options(stream_results=True) as con:
        result = con.execute(_QUERY, params)
        while batch := result.fetchmany(batch_size):
            for row in batch:
                attributes = json.loads(row.attributes_json) if row.attributes_json else None
                yield BackfilledStateChange(
                    entity_id=row.entity_id,
                    state=row.state,
                    attributes=attributes,
                    old_state=row.old_state,
                    last_changed=_to_utc(row.last_changed_ts),
                    last_updated=_to_utc(row.last_updated_ts),
                    context_id=bytes_to_ulid_or_none(row.context_id_bin),
                    context_parent_id=bytes_to_ulid_or_none(row.context_parent_id_bin),
                    context_user_id=row.context_user_id_bin.hex()
                    if row.context_user_id_bin is not None
                    else None,
                )


def run_backfill(
    store: EventStore,
    db_path: str | Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> BackfillSummary:
    """Read the recorder database at ``db_path`` and write everything in
    ``[since, until)`` into ``store``. Either bound may be omitted to mean
    "no limit" — omitting both backfills the recorder's entire retained history."""
    engine = sa.create_engine(sqlite_read_only_url(db_path))
    try:
        schema_version = _schema_version(engine)
        _check_required_columns(engine, schema_version)
        logger.info(
            "backfill_starting", db_path=str(db_path), schema_version=schema_version
        )

        rows_read = 0
        earliest: datetime | None = None
        latest: datetime | None = None
        for row in read_state_changes(engine, since=since, until=until):
            store.write_state_change(
                entity_id=row.entity_id,
                state=row.state,
                attributes=row.attributes,
                old_state=row.old_state,
                last_changed=row.last_changed,
                last_updated=row.last_updated,
                context_id=row.context_id,
                context_parent_id=row.context_parent_id,
                context_user_id=row.context_user_id,
                source="backfill",
            )
            rows_read += 1
            if row.last_updated is not None:
                earliest = row.last_updated if earliest is None else min(earliest, row.last_updated)
                latest = row.last_updated if latest is None else max(latest, row.last_updated)
            if rows_read % _PROGRESS_LOG_INTERVAL == 0:
                logger.info("backfill_progress", rows_read=rows_read)

        logger.info(
            "backfill_complete", rows_read=rows_read, earliest=earliest, latest=latest
        )
        return BackfillSummary(
            rows_read=rows_read,
            rows_written=rows_read,
            schema_version=schema_version,
            earliest=earliest,
            latest=latest,
        )
    finally:
        engine.dispose()
