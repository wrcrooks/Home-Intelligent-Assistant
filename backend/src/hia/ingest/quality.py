"""A first data-quality report over the event store.

docs/02-architecture.md asks for "entities with no history, sensors that flap, units
that change, gaps in the stream." This is the v1 slice of that: no-history and stale
entities, and gap detection via the ``resumed_after_gap`` flag every row already
carries. Flapping-sensor and unit-change detection need enough real data to define
sensibly (what counts as "flapping" is itself a modelling question) and are deferred
to P4, once there's a real house's worth of history to calibrate against — tracked in
docs/HANDOFF.md, not silently dropped.

This reads the store; it never writes to it, and it takes no dependency on the
Home Assistant client — a report can be produced at any time from just the DB file,
independent of whether ingest is currently running.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hia.ingest.store import EntityActivity, EventStore

DEFAULT_STALE_AFTER = timedelta(hours=24)
"""An entity with no new row in this long is flagged stale — either it stopped
reporting, or (for a house) it may just be an entity nobody interacts with. Either
way, worth surfacing rather than silently trusting a feature built from it."""


@dataclass(frozen=True, slots=True)
class QualityReport:
    generated_at: datetime
    total_state_changes: int
    total_events: int
    tracked_entities: int
    stale_entities: list[EntityActivity]
    gap_resumption_count: int
    """Rows stored as the first event after a client reconnect. Not a count of
    missed events (those are, by definition, not in the store) — a count of how many
    times the live window had one, as a prompt to check recorder backfill coverage
    for those windows once P1's backfill slice exists."""


def build_report(
    store: EventStore, *, stale_after: timedelta = DEFAULT_STALE_AFTER
) -> QualityReport:
    now = datetime.now(UTC)
    activity = store.entity_activity()
    stale = [a for a in activity if now - a.last_seen > stale_after]
    return QualityReport(
        generated_at=now,
        total_state_changes=store.state_change_count(),
        total_events=store.event_count(),
        tracked_entities=len(activity),
        stale_entities=sorted(stale, key=lambda a: a.last_seen),
        gap_resumption_count=store.gap_resumption_count(),
    )


def format_report(report: QualityReport) -> str:
    lines = [
        f"Data quality report — {report.generated_at.isoformat()}",
        f"  state_changes: {report.total_state_changes}",
        f"  events:        {report.total_events}",
        f"  entities:      {report.tracked_entities}",
        f"  gap resumptions: {report.gap_resumption_count} "
        "(reconnects where the live window may have missed events)",
    ]
    if report.stale_entities:
        lines.append(f"  stale entities (no update in >24h): {len(report.stale_entities)}")
        for a in report.stale_entities[:20]:
            lines.append(f"    {a.entity_id}: last seen {a.last_seen.isoformat()}")
        if len(report.stale_entities) > 20:
            lines.append(f"    ... and {len(report.stale_entities) - 20} more")
    else:
        lines.append("  stale entities: none")
    return "\n".join(lines)
