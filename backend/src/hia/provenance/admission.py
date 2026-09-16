"""Admission control (docs/05-provenance.md §6): decides whether a decision
point is even worth building for a given entity, *before* any reward gating
happens. Two independent questions per entity, both computed over a trailing
window (default 30 days, matching the docs):

- **automation_share** — what fraction of this entity's state changes are
  already explained by a rule (Layer 1/2/3's ``automation_ha``/
  ``automation_external`` kinds — see :mod:`hia.provenance.classify`)? A high
  share means "this is already automated" — building a decision point here
  could only ever learn to predict what a rule already does, which is exactly
  the pointless-learning case reward gating alone does not stop.
- **effective_human_events_per_week** — of the events *not* explained by a
  rule, how many are confidently human (``human_ui``/``human_voice``)? This is
  the actual training signal a decision point would have to learn from; below
  a floor, there simply isn't enough of it, regardless of ``automation_share``.
  docs/05-provenance.md §6 calls this "the headline metric".

**Whole-entity exclusion only** — docs/05-provenance.md §6 is explicit that
conditional exclusion (excluding only the tautological region a rule's own
conditions cover, e.g. "porch light at sunset is covered, at 02:00 is not") is
a refinement on top of this, not part of the first version.

**Collision detection** (the other half of §6 — "an automation also targets
this entity") is implemented here as *automation involvement*: which specific
automations/scripts have actually caused changes to each entity, surfaced as
``responsible_automations``. It is not yet checked against a live decision
point in conflict, since no decision points exist yet (P6) — there is nothing
to collide with. What this module answers now is ready to feed that check the
moment P6 exists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from hia.ingest.store import EventStore
from hia.provenance.chain import ContextChainResolver
from hia.provenance.classify import classify
from hia.provenance.correlate import AutomationCorrelator, automation_firings

WINDOW_DAYS = 30
"""docs/05-provenance.md §6: "over a trailing 30-day window"."""

EXCLUDE_ABOVE_SHARE = 0.8
WARN_ABOVE_SHARE = 0.3
"""The two thresholds from docs/05-provenance.md §6's table: > 0.8 excluded by
default, 0.3-0.8 allowed with a warning, < 0.3 normal."""

EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK = 5.0
"""docs/05-provenance.md §6: "Below a floor (~5/week), it is disabled with an
explicit message: not enough human signal to learn from"."""

Treatment = Literal["excluded", "warned", "normal"]


@dataclass(frozen=True, slots=True)
class EntityAdmission:
    entity_id: str
    total_state_changes: int
    """Total classified state changes in the window — the denominator behind
    every rate below, and worth showing on its own: a tiny total makes every
    other number here unreliable regardless of what it says."""
    automation_share: float
    treatment: Treatment
    responsible_automations: tuple[str, ...]
    """The automation/script entity_ids actually observed causing changes to
    this entity, most-frequent first — empty if none (a share of 0 has nothing
    to list; a nonzero share with an empty tuple means Layer 2 correlation
    matched without Layer 1 ever resolving a named source)."""
    effective_human_events_per_week: float
    learnable: bool
    """False when effective_human_events_per_week is below
    EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK — not enough human signal to learn
    from, regardless of what automation_share says."""


def compute_admission(
    store: EventStore,
    targets: dict[str, frozenset[str]],
    *,
    window_days: int = WINDOW_DAYS,
    now: datetime | None = None,
) -> list[EntityAdmission]:
    """Runs the full Layer 1+2+3 classifier (:mod:`hia.provenance.classify`)
    over every state change in the trailing window and aggregates per entity.
    ``targets`` is Layer 2's automation-target map — see
    :func:`hia.ha.automations.fetch_targets` and ``hia.provenance.report`` for
    the same pattern used to build it. ``now`` is injectable for tests; real
    callers should leave it at the actual current time."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=window_days)

    events = store.events_for_provenance(since=since)
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), targets)
    actor_classifications = store.actor_classifications()

    rows = store.state_changes_for_provenance(since=since)

    totals: Counter[str] = Counter()
    automation_counts: Counter[str] = Counter()
    human_counts: Counter[str] = Counter()
    sources: dict[str, Counter[str]] = {}

    for row in rows:
        totals[row.entity_id] += 1
        result = classify(
            entity_id=row.entity_id,
            changed_at=row.changed_at,
            context_id=row.context_id,
            context_parent_id=row.context_parent_id,
            chain=chain,
            correlator=correlator,
            actor_classifications=actor_classifications,
        )
        if result.kind in ("automation_ha", "automation_external"):
            automation_counts[row.entity_id] += 1
            if result.source_entity_id:
                sources.setdefault(row.entity_id, Counter())[result.source_entity_id] += 1
        elif result.kind in ("human_ui", "human_voice"):
            human_counts[row.entity_id] += 1

    weeks = window_days / 7
    admissions = []
    for entity_id, total in totals.items():
        share = automation_counts[entity_id] / total
        effective_per_week = human_counts[entity_id] / weeks

        treatment: Treatment
        if share > EXCLUDE_ABOVE_SHARE:
            treatment = "excluded"
        elif share > WARN_ABOVE_SHARE:
            treatment = "warned"
        else:
            treatment = "normal"

        admissions.append(
            EntityAdmission(
                entity_id=entity_id,
                total_state_changes=total,
                automation_share=share,
                treatment=treatment,
                responsible_automations=tuple(
                    s for s, _ in sources.get(entity_id, Counter()).most_common()
                ),
                effective_human_events_per_week=effective_per_week,
                learnable=effective_per_week >= EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK,
            )
        )

    return sorted(admissions, key=lambda a: a.entity_id)


def format_admission_report(admissions: list[EntityAdmission]) -> str:
    excluded = [a for a in admissions if a.treatment == "excluded"]
    warned = [a for a in admissions if a.treatment == "warned"]
    normal = [a for a in admissions if a.treatment == "normal"]
    unlearnable = [a for a in admissions if not a.learnable]

    lines = [
        f"Admission control report ({len(admissions)} entities, "
        f"trailing {WINDOW_DAYS}-day window)",
        f"  excluded (automation_share > {EXCLUDE_ABOVE_SHARE:.0%}): {len(excluded)}",
        f"  warned (automation_share {WARN_ABOVE_SHARE:.0%}-{EXCLUDE_ABOVE_SHARE:.0%}): {len(warned)}",
        f"  normal (automation_share < {WARN_ABOVE_SHARE:.0%}): {len(normal)}",
        f"  not learnable (< {EFFECTIVE_HUMAN_EVENTS_FLOOR_PER_WEEK:.0f} effective "
        f"human events/week): {len(unlearnable)}",
    ]
    if excluded:
        lines.append("  excluded entities:")
        for a in excluded[:20]:
            sources = ", ".join(a.responsible_automations[:3]) or "unattributed"
            lines.append(
                f"    {a.entity_id}: share={a.automation_share:.0%} ({sources})"
            )
        if len(excluded) > 20:
            lines.append(f"    ... and {len(excluded) - 20} more")
    return "\n".join(lines)
