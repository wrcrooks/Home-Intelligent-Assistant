"""Runs the full Layer 1 + Layer 2 + Layer 3 classifier
(:mod:`hia.provenance.classify`) over every ``state_changes`` row currently in
the store and summarizes the result.

This is *not* the P3 exit criterion itself (docs/04-roadmap.md: a hand-labelled
200-row sample classified with >95% precision on the human classes) — that needs
real house data and manual labelling no single session can produce. What this
gives instead: a per-kind and per-source breakdown of how much of the store's
history this classifier can already explain, useful both as a sanity check while
iterating on it and as a first look at whether a given house has automations, or
tagged actors, worth anything yet.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from hia.ingest.store import EventStore
from hia.provenance.chain import ContextChainResolver
from hia.provenance.classify import ProvenanceKind, ProvenanceResult, classify
from hia.provenance.correlate import AutomationCorrelator, automation_firings


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    total_state_changes: int
    by_kind: Counter[ProvenanceKind]
    by_source_entity: Counter[str]
    """automation/script entity_id -> how many state changes were attributed to
    it (``automation_ha``/``automation_external`` rows only). The closest thing
    this project has today to ``automation_share`` (docs/05-provenance.md §6) —
    real admission control still needs a trailing-30-day window and collision
    detection, not built yet, but this is already useful as a first look at
    which automations dominate a house's event stream."""


def build_report(
    store: EventStore, targets: dict[str, frozenset[str]]
) -> ProvenanceReport:
    events = store.events_for_provenance()
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), targets)
    actor_classifications = store.actor_classifications()

    by_kind: Counter[ProvenanceKind] = Counter()
    by_source: Counter[str] = Counter()
    rows = store.state_changes_for_provenance()

    for row in rows:
        result: ProvenanceResult = classify(
            entity_id=row.entity_id,
            changed_at=row.changed_at,
            context_id=row.context_id,
            context_parent_id=row.context_parent_id,
            chain=chain,
            correlator=correlator,
            actor_classifications=actor_classifications,
        )
        by_kind[result.kind] += 1
        if result.source_entity_id:
            by_source[result.source_entity_id] += 1

    return ProvenanceReport(
        total_state_changes=len(rows), by_kind=by_kind, by_source_entity=by_source
    )


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total else "n/a"


def format_report(report: ProvenanceReport) -> str:
    total = report.total_state_changes

    def pct(n: int) -> str:
        return _pct(n, total)

    lines = [
        "Provenance report (Layer 1 + Layer 2 + Layer 3 -- "
        "human_physical/device_local/hia_self not classifiable yet)",
        f"  state_changes classified: {total}",
    ]
    all_kinds: tuple[ProvenanceKind, ...] = (
        "human_ui",
        "human_voice",
        "automation_ha",
        "automation_external",
        "unknown",
    )
    for kind in all_kinds:
        count = report.by_kind[kind]
        lines.append(f"  {kind}: {count} ({pct(count)})")
    if report.by_source_entity:
        lines.append("  top automation sources:")
        for entity_id, count in report.by_source_entity.most_common(20):
            lines.append(f"    {entity_id}: {count}")
    return "\n".join(lines)
