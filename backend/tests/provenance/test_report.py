"""hia.provenance.report: runs the classifier over a whole store and summarizes
it — the CLI-facing piece (`hia provenance-report`)."""

from __future__ import annotations

from hia.ingest.store import EventStore
from hia.provenance.report import build_report, format_report
from tests.ingest.factories import make_generic_watched, make_state_changed_watched


def test_report_splits_automation_and_unknown_and_credits_the_right_source() -> None:
    with EventStore(":memory:") as store:
        # A sun-triggered automation: fires with no context at all, and its
        # target changes state shortly after -- only Layer 2 can explain this.
        store.write_watched_event(
            make_generic_watched(1, "automation_triggered", {"entity_id": "automation.porch"})
        )
        store.write_watched_event(make_state_changed_watched(2, "light.porch"))
        # A genuinely unexplained change -- no automation, no correlating firing.
        store.write_watched_event(make_state_changed_watched(3, "light.kitchen"))

        report = build_report(store, {"automation.porch": frozenset({"light.porch"})})

    assert report.total_state_changes == 2
    assert report.layer2_count == 1
    assert report.unknown_count == 1
    assert report.by_source_entity["automation.porch"] == 1
    # A sanity check on the formatter too, since the CLI just prints this.
    assert "layer 2" in format_report(report).lower()
