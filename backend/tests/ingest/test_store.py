from __future__ import annotations

from pathlib import Path

from hia.ingest.store import EventStore
from tests.ingest.factories import make_generic_watched, make_state_changed_watched


def test_writes_state_changed_into_state_changes_table() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_state_changed_watched(1, "light.kitchen", new_state="on", old_state="off")
        )
        assert store.state_change_count() == 1
        assert store.event_count() == 0
        [activity] = store.entity_activity()
        assert activity.entity_id == "light.kitchen"
        assert activity.row_count == 1


def test_writes_non_state_changed_into_events_table() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_generic_watched(1, "automation_triggered", {"entity_id": "automation.sunset"})
        )
        assert store.event_count() == 1
        assert store.state_change_count() == 0


def test_gap_resumption_count_only_counts_flagged_rows() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_state_changed_watched(1, "light.a", resumed_after_gap=False)
        )
        store.write_watched_event(
            make_state_changed_watched(2, "light.b", resumed_after_gap=True)
        )
        store.write_watched_event(
            make_generic_watched(3, "call_service", {}, resumed_after_gap=True)
        )
        assert store.gap_resumption_count() == 2


def test_context_and_old_state_are_captured() -> None:
    """The two things docs/05-provenance.md needs from every row: who caused it
    (context, including user_id — the Node-RED/automation-attribution signal), and
    what the entity's state was immediately before (for gap/discontinuity checks)."""
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_state_changed_watched(
                1, "light.a", new_state="on", old_state="off", context_user_id="user-123"
            )
        )
        row = store._con.execute(
            "SELECT context_user_id, old_state, state FROM state_changes"
        ).fetchone()
        assert row == ("user-123", "off", "on")


def test_reopening_a_file_backed_store_preserves_data(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    with EventStore(db_path) as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a"))
    with EventStore(db_path) as store:
        assert store.state_change_count() == 1
