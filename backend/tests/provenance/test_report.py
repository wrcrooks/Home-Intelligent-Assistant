"""hia.provenance.report: runs the classifier over a whole store and summarizes
it — the CLI-facing piece (`hia provenance-report`)."""

from __future__ import annotations

from hia.ingest.store import EventStore
from hia.provenance.report import build_report, format_report
from tests.ingest.factories import make_generic_watched, make_state_changed_watched


def test_report_splits_by_kind_and_credits_the_right_source() -> None:
    with EventStore(":memory:") as store:
        # A sun-triggered automation: fires with no context at all, and its
        # target changes state shortly after -- only Layer 2 can explain this.
        store.write_watched_event(
            make_generic_watched(
                1, "automation_triggered", {"entity_id": "automation.porch"}, context_id="ctx-fire"
            )
        )
        store.write_watched_event(make_state_changed_watched(2, "light.porch", context_id="ctx-porch"))
        # A confirmed human actor: a bare call_service carrying their user_id,
        # and the light change chains to it via context_parent_id.
        store.write_watched_event(
            make_generic_watched(
                3, "call_service", {"domain": "light"}, context_id="ctx-call", context_user_id="user-will"
            )
        )
        store.write_watched_event(
            make_state_changed_watched(4, "light.hall", context_id="ctx-hall", context_parent_id="ctx-call")
        )
        store.set_actor_classification("user-will", "human")
        # A genuinely unexplained change -- no automation, no correlating firing.
        store.write_watched_event(make_state_changed_watched(5, "light.kitchen", context_id="ctx-kitchen"))

        report = build_report(store, {"automation.porch": frozenset({"light.porch"})})

    assert report.total_state_changes == 3
    assert report.by_kind["automation_ha"] == 1
    assert report.by_kind["human_ui"] == 1
    assert report.by_kind["unknown"] == 1
    assert report.by_source_entity["automation.porch"] == 1
    # A sanity check on the formatter too, since the CLI just prints this.
    formatted = format_report(report)
    assert "automation_ha: 1" in formatted
    assert "human_ui: 1" in formatted
