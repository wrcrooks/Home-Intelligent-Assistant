"""Builds a small SQLite database shaped like Home Assistant's recorder — just the
tables and columns ``hia.ingest.backfill`` actually reads, with real column names and
real ULID/UUID byte encodings — so backfill can be tested without a live HA instance.

Column presence is parameterised (``omit_columns``) specifically to exercise the
"refuse to guess at an unsupported schema" path in ``hia.ingest.backfill``.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from ulid_transform import ulid_to_bytes

_STATES_COLUMNS: dict[str, str] = {
    "state_id": "INTEGER PRIMARY KEY",
    "state": "TEXT",
    "last_changed_ts": "REAL",
    "last_updated_ts": "REAL",
    "old_state_id": "INTEGER",
    "attributes_id": "INTEGER",
    "context_id_bin": "BLOB",
    "context_user_id_bin": "BLOB",
    "context_parent_id_bin": "BLOB",
    "metadata_id": "INTEGER",
}


def create_recorder_db(
    path: Path, *, schema_version: int = 53, omit_columns: frozenset[str] = frozenset()
) -> sqlite3.Connection:
    """Creates the database and returns an open (read-write) connection for the
    caller to insert fixture rows with, before pointing the read-only backfill
    reader at the same file."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE schema_changes "
        "(change_id INTEGER PRIMARY KEY, schema_version INTEGER, changed TEXT)"
    )
    con.execute(
        "INSERT INTO schema_changes (schema_version, changed) VALUES (?, ?)",
        (schema_version, "2026-01-01T00:00:00"),
    )
    con.execute("CREATE TABLE states_meta (metadata_id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE)")
    con.execute("CREATE TABLE state_attributes (attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT)")

    kept = {name: ddl for name, ddl in _STATES_COLUMNS.items() if name not in omit_columns}
    con.execute(f"CREATE TABLE states ({', '.join(f'{n} {d}' for n, d in kept.items())})")
    con.commit()
    return con


def insert_entity(con: sqlite3.Connection, metadata_id: int, entity_id: str) -> None:
    con.execute(
        "INSERT INTO states_meta (metadata_id, entity_id) VALUES (?, ?)",
        (metadata_id, entity_id),
    )


def insert_attributes(con: sqlite3.Connection, attributes_id: int, shared_attrs_json: str) -> None:
    con.execute(
        "INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (?, ?)",
        (attributes_id, shared_attrs_json),
    )


def insert_state(
    con: sqlite3.Connection,
    *,
    state_id: int,
    metadata_id: int,
    state: str,
    last_updated_ts: float,
    last_changed_ts: float | None = None,
    old_state_id: int | None = None,
    attributes_id: int | None = None,
    context_id: str | None = None,
    context_parent_id: str | None = None,
    context_user_id: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO states (
            state_id, state, last_changed_ts, last_updated_ts, old_state_id,
            attributes_id, context_id_bin, context_user_id_bin, context_parent_id_bin,
            metadata_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            state_id,
            state,
            last_changed_ts if last_changed_ts is not None else last_updated_ts,
            last_updated_ts,
            old_state_id,
            attributes_id,
            ulid_to_bytes(context_id) if context_id else None,
            uuid.UUID(hex=context_user_id).bytes if context_user_id else None,
            ulid_to_bytes(context_parent_id) if context_parent_id else None,
            metadata_id,
        ),
    )
    con.commit()
