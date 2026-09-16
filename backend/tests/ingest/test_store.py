from __future__ import annotations

from pathlib import Path

import pytest

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


def test_latest_states_picks_the_most_recently_written_row_on_a_timestamp_tie() -> None:
    """The fixtures share a fixed clock (deliberately, for reproducibility), so this
    also guards the real case it stands in for: two real rows landing in the same
    HA timestamp tick. Ties must break on insertion order, not arbitrarily."""
    with EventStore(":memory:") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a", new_state="off"))
        store.write_watched_event(
            make_state_changed_watched(2, "light.a", new_state="on", old_state="off")
        )
        store.write_watched_event(make_state_changed_watched(3, "light.b", new_state="on"))

        latest = {s.entity_id: s.state for s in store.latest_states()}
        assert latest == {"light.a": "on", "light.b": "on"}


def test_read_only_store_requires_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        EventStore(tmp_path / "does-not-exist.duckdb", read_only=True)


def test_read_only_store_sees_data_written_before_it_was_opened(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    with EventStore(db_path) as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a"))

    with EventStore(db_path, read_only=True) as reader:
        assert reader.state_change_count() == 1
        assert reader.latest_states()[0].entity_id == "light.a"


def test_events_for_provenance_returns_only_the_three_provenance_event_types() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_generic_watched(1, "automation_triggered", {"entity_id": "automation.a"})
        )
        store.write_watched_event(make_generic_watched(2, "call_service", {"domain": "light"}))
        store.write_watched_event(make_generic_watched(3, "some_other_event", {}))

        rows = store.events_for_provenance()
        assert {r.event_type for r in rows} == {"automation_triggered", "call_service"}
        automation_row = next(r for r in rows if r.event_type == "automation_triggered")
        assert automation_row.data == {"entity_id": "automation.a"}


def test_state_changes_for_provenance_carries_context_columns() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_state_changed_watched(1, "light.a", context_parent_id="ctx-parent")
        )
        [row] = store.state_changes_for_provenance()
        assert row.entity_id == "light.a"
        assert row.context_id == "ctx-1"
        assert row.context_parent_id == "ctx-parent"


def test_actor_classification_round_trips_and_upserts() -> None:
    with EventStore(":memory:") as store:
        assert store.actor_classifications() == {}

        store.set_actor_classification("user-1", "human")
        assert store.actor_classifications() == {"user-1": "human"}

        # Re-confirming replaces the prior classification rather than erroring
        # or duplicating -- a person's role can change.
        store.set_actor_classification("user-1", "service_account")
        assert store.actor_classifications() == {"user-1": "service_account"}


def test_user_event_timestamps_collects_across_both_tables() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(
            make_state_changed_watched(1, "light.a", context_user_id="user-1")
        )
        store.write_watched_event(
            make_generic_watched(2, "call_service", {}, context_user_id="user-1")
        )
        # No context_user_id at all -- HA's automation engine, not a person --
        # must not show up as an "observed actor".
        store.write_watched_event(make_generic_watched(3, "automation_triggered", {}))

        timestamps = store.user_event_timestamps()

        assert list(timestamps.keys()) == ["user-1"]
        assert len(timestamps["user-1"]) == 2
