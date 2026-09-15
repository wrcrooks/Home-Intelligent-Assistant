from __future__ import annotations

from datetime import timedelta

from hia.ingest.quality import build_report, format_report
from hia.ingest.store import EventStore
from tests.ingest.factories import make_state_changed_watched


def test_report_counts_totals_and_does_not_flag_a_fresh_entity_stale() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.fresh"))
        report = build_report(store, stale_after=timedelta(hours=24))
        assert report.total_state_changes == 1
        assert report.tracked_entities == 1
        assert report.stale_entities == []


def test_report_flags_an_entity_written_before_the_stale_threshold() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.old"))
        # ingested_at is set to "now" by the store; a threshold of 0 makes every
        # entity immediately stale without needing to fake the clock.
        report = build_report(store, stale_after=timedelta(seconds=0))
        assert [a.entity_id for a in report.stale_entities] == ["light.old"]


def test_format_report_is_human_readable() -> None:
    with EventStore(":memory:") as store:
        store.write_watched_event(make_state_changed_watched(1, "light.a"))
        text = format_report(build_report(store))
        assert "state_changes: 1" in text
        assert "stale entities: none" in text
