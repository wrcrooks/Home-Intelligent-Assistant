"""hia.provenance.admission: automation_share, the exclusion/warning
thresholds, and the effective-human-events-per-week floor
(docs/05-provenance.md §6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hia.ha.models import Context, HAEvent
from hia.ingest.store import EventStore
from hia.provenance.admission import (
    EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK,
    compute_admission,
    format_admission_report,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _write_automation_fire(
    store: EventStore, automation_entity_id: str, context_id: str, ts: datetime
) -> None:
    store.write_event(
        HAEvent(
            event_type="automation_triggered",
            data={"entity_id": automation_entity_id},
            origin="LOCAL",
            time_fired=ts,
            context=Context(id=context_id),
        ),
        source="live",
    )


def _write_call_service(store: EventStore, context_id: str, ts: datetime, *, user_id: str) -> None:
    store.write_event(
        HAEvent(
            event_type="call_service",
            data={"domain": "light", "service": "turn_on"},
            origin="LOCAL",
            time_fired=ts,
            context=Context(id=context_id, user_id=user_id),
        ),
        source="live",
    )


def _write_state_change(
    store: EventStore,
    entity_id: str,
    ts: datetime,
    *,
    context_id: str,
    context_parent_id: str | None = None,
) -> None:
    store.write_state_change(
        entity_id=entity_id,
        state="on",
        attributes=None,
        old_state=None,
        last_changed=ts,
        last_updated=ts,
        context_id=context_id,
        context_parent_id=context_parent_id,
        context_user_id=None,
        source="live",
    )


def test_high_automation_share_is_excluded_and_lists_the_source() -> None:
    with EventStore(":memory:") as store:
        for i in range(9):
            ts = NOW - timedelta(hours=i)
            _write_automation_fire(store, "automation.porch", f"ctx-fire-{i}", ts)
            _write_state_change(
                store, "light.porch", ts, context_id=f"ctx-sc-{i}", context_parent_id=f"ctx-fire-{i}"
            )
        # One human-caused change on the same entity, via a confirmed actor.
        _write_call_service(store, "ctx-call", NOW, user_id="user-will")
        _write_state_change(store, "light.porch", NOW, context_id="ctx-human", context_parent_id="ctx-call")
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW)

    [porch] = [a for a in admissions if a.entity_id == "light.porch"]
    assert porch.total_state_changes == 10
    assert porch.automation_share == 0.9
    assert porch.treatment == "excluded"
    assert porch.responsible_automations == ("automation.porch",)


def test_moderate_share_is_warned() -> None:
    with EventStore(":memory:") as store:
        for i in range(4):
            ts = NOW - timedelta(hours=i)
            _write_automation_fire(store, "automation.hall", f"ctx-fire-{i}", ts)
            _write_state_change(
                store, "light.hall", ts, context_id=f"ctx-sc-{i}", context_parent_id=f"ctx-fire-{i}"
            )
        for i in range(6):
            ts = NOW - timedelta(hours=10 + i)
            _write_call_service(store, f"ctx-call-{i}", ts, user_id="user-will")
            _write_state_change(
                store, "light.hall", ts, context_id=f"ctx-human-{i}", context_parent_id=f"ctx-call-{i}"
            )
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW)

    [hall] = [a for a in admissions if a.entity_id == "light.hall"]
    assert hall.total_state_changes == 10
    assert hall.automation_share == 0.4
    assert hall.treatment == "warned"


def test_low_share_is_normal() -> None:
    with EventStore(":memory:") as store:
        _write_automation_fire(store, "automation.kitchen", "ctx-fire-0", NOW)
        _write_state_change(store, "light.kitchen", NOW, context_id="ctx-sc-0", context_parent_id="ctx-fire-0")
        for i in range(9):
            ts = NOW - timedelta(hours=i + 1)
            _write_call_service(store, f"ctx-call-{i}", ts, user_id="user-will")
            _write_state_change(
                store, "light.kitchen", ts, context_id=f"ctx-human-{i}", context_parent_id=f"ctx-call-{i}"
            )
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW)

    [kitchen] = [a for a in admissions if a.entity_id == "light.kitchen"]
    assert kitchen.total_state_changes == 10
    assert kitchen.automation_share == 0.1
    assert kitchen.treatment == "normal"


def test_effective_human_events_per_week_and_learnable_floor() -> None:
    with EventStore(":memory:") as store:
        # A well-populated entity: 30 human events over 30 days -> ~7/week.
        for i in range(30):
            ts = NOW - timedelta(days=i)
            _write_call_service(store, f"ctx-a-{i}", ts, user_id="user-will")
            _write_state_change(
                store, "light.busy", ts, context_id=f"ctx-a-sc-{i}", context_parent_id=f"ctx-a-{i}"
            )
        # A sparse entity: only 2 human events over 30 days -- well under the floor.
        for i in range(2):
            ts = NOW - timedelta(days=i * 10)
            _write_call_service(store, f"ctx-b-{i}", ts, user_id="user-will")
            _write_state_change(
                store, "light.rare", ts, context_id=f"ctx-b-sc-{i}", context_parent_id=f"ctx-b-{i}"
            )
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW)

    by_id = {a.entity_id: a for a in admissions}
    assert by_id["light.busy"].learnable is True
    assert by_id["light.busy"].effective_human_events_per_week > EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK
    assert by_id["light.rare"].learnable is False
    assert by_id["light.rare"].effective_human_events_per_week < EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK


def test_rows_outside_the_window_are_excluded_entirely() -> None:
    with EventStore(":memory:") as store:
        old = NOW - timedelta(days=40)
        _write_call_service(store, "ctx-old", old, user_id="user-will")
        _write_state_change(store, "light.stale", old, context_id="ctx-old-sc", context_parent_id="ctx-old")
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW, window_days=30)

    assert "light.stale" not in {a.entity_id for a in admissions}


def test_format_admission_report_is_human_readable() -> None:
    with EventStore(":memory:") as store:
        for i in range(9):
            ts = NOW - timedelta(hours=i)
            _write_automation_fire(store, "automation.porch", f"ctx-fire-{i}", ts)
            _write_state_change(
                store, "light.porch", ts, context_id=f"ctx-sc-{i}", context_parent_id=f"ctx-fire-{i}"
            )
        _write_call_service(store, "ctx-call", NOW, user_id="user-will")
        _write_state_change(store, "light.porch", NOW, context_id="ctx-human", context_parent_id="ctx-call")
        store.set_actor_classification("user-will", "human")

        admissions = compute_admission(store, targets={}, now=NOW)

    report = format_admission_report(admissions)
    assert "excluded" in report
    assert "light.porch" in report
    assert "automation.porch" in report
