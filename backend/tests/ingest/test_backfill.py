"""hia.ingest.backfill against a synthetic recorder-shaped SQLite database
(tests/ingest/recorder_fixture.py) — no live HA instance needed for these. Verified
separately against a real recorder database; see docs/HANDOFF.md.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from ulid_transform import ulid_now

from hia.ingest.backfill import (
    UnsupportedRecorderSchemaError,
    run_backfill,
    sqlite_read_only_url,
)
from hia.ingest.store import EventStore
from tests.ingest.recorder_fixture import (
    create_recorder_db,
    insert_attributes,
    insert_entity,
    insert_state,
)

CONTEXT_A = ulid_now()
CONTEXT_B = ulid_now()
USER_ID = uuid.uuid4().hex


def _build_basic_db(path: Path) -> sqlite3.Connection:
    con = create_recorder_db(path)
    insert_entity(con, 1, "light.kitchen")
    insert_attributes(con, 1, '{"brightness": 128}')
    insert_state(
        con,
        state_id=1,
        metadata_id=1,
        state="off",
        last_updated_ts=1_700_000_000.0,
        context_id=CONTEXT_A,
        context_user_id=USER_ID,
    )
    insert_state(
        con,
        state_id=2,
        metadata_id=1,
        state="on",
        last_updated_ts=1_700_000_060.0,
        old_state_id=1,
        attributes_id=1,
        context_id=CONTEXT_B,
        context_parent_id=CONTEXT_A,
    )
    return con


def test_sqlite_read_only_url_rejects_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sqlite_read_only_url(tmp_path / "does-not-exist.db")


def test_read_only_connection_cannot_write(tmp_path: Path) -> None:
    """The one thing that must never regress silently: CLAUDE.md's "the recorder DB
    is read-only, always." A malformed URL that quietly drops the ro flag would be
    exactly the kind of bug this project is designed to avoid."""
    db_path = tmp_path / "recorder.db"
    _build_basic_db(db_path).close()

    engine = sa.create_engine(sqlite_read_only_url(db_path))
    with pytest.raises(sa.exc.OperationalError, match="readonly|read-only"), engine.connect() as con:
        con.execute(sa.text("INSERT INTO states_meta (metadata_id, entity_id) VALUES (99, 'x')"))
        con.commit()
    engine.dispose()


def test_run_backfill_writes_state_history_with_correct_context(tmp_path: Path) -> None:
    db_path = tmp_path / "recorder.db"
    _build_basic_db(db_path).close()

    with EventStore(":memory:") as store:
        summary = run_backfill(store, db_path)

        assert summary.rows_written == 2
        assert summary.schema_version == 53

        row = store._con.execute(
            "SELECT entity_id, state, old_state, attributes, context_id, "
            "context_parent_id, context_user_id, source "
            "FROM state_changes WHERE state = 'on'"
        ).fetchone()
        assert row[0] == "light.kitchen"
        assert row[1] == "on"
        assert row[2] == "off"  # old_state, via the old_state_id self-join
        assert row[3] == '{"brightness": 128}'
        assert row[4] == CONTEXT_B  # context_id_bin decoded back to the same ULID
        assert row[5] == CONTEXT_A  # context_parent_id likewise
        assert row[6] is None  # this row never had a context_user_id_bin
        assert row[7] == "backfill"

        # the *other* row's context_user_id round-trips correctly too
        user_row = store._con.execute(
            "SELECT context_user_id FROM state_changes WHERE state = 'off'"
        ).fetchone()
        assert user_row[0] == USER_ID


def test_since_and_until_filter_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "recorder.db"
    _build_basic_db(db_path).close()

    from datetime import UTC, datetime

    with EventStore(":memory:") as store:
        summary = run_backfill(
            store,
            db_path,
            since=datetime.fromtimestamp(1_700_000_030.0, tz=UTC),
        )
        assert summary.rows_written == 1
        assert store._con.execute("SELECT state FROM state_changes").fetchone()[0] == "on"


def test_missing_required_column_raises_unsupported_schema_error(tmp_path: Path) -> None:
    db_path = tmp_path / "old_recorder.db"
    create_recorder_db(db_path, schema_version=20, omit_columns=frozenset({"metadata_id"})).close()

    with EventStore(":memory:") as store, pytest.raises(UnsupportedRecorderSchemaError, match="metadata_id"):
        run_backfill(store, db_path)


def test_not_a_recorder_database_raises_unsupported_schema_error(tmp_path: Path) -> None:
    db_path = tmp_path / "not_recorder.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE unrelated (id INTEGER)")
    con.commit()
    con.close()

    with EventStore(":memory:") as store, pytest.raises(UnsupportedRecorderSchemaError):
        run_backfill(store, db_path)
