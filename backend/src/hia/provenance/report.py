"""Runs the Layer 1 + Layer 2 classifier (:mod:`hia.provenance.classify`) over
every ``state_changes`` row currently in the store and summarizes the result.

This is *not* the P3 exit criterion itself (docs/04-roadmap.md: a hand-labelled
200-row sample classified with >95% precision on the human classes) — that needs
real house data and manual labelling no single session can produce. What this
gives instead: a per-automation and per-entity breakdown of how much of the
store's history this slice can already explain as machine-caused, useful both as
a sanity check while iterating on the classifier and as a first look at whether a
given house has automations at all worth correlating against.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from hia.ingest.store import EventStore
from hia.provenance.chain import ContextChainResolver
from hia.provenance.classify import ProvenanceResult, classify
from hia.provenance.correlate import AutomationCorrelator, automation_firings


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    total_state_changes: int
    automation_count: int
    layer1_count: int
    layer2_count: int
    unknown_count: int
    by_source_entity: Counter[str]
    """automation/script entity_id -> how many state changes were attributed to
    it, across both layers. The closest thing this slice has to
    ``automation_share`` (docs/05-provenance.md §6) — real admission control needs
    Layer 3 too, but this is already useful as a first look at which automations
    dominate a house's event stream."""


def build_report(
    store: EventStore, targets: dict[str, frozenset[str]]
) -> ProvenanceReport:
    events = store.events_for_provenance()
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), targets)

    by_source: Counter[str] = Counter()
    layer1 = layer2 = unknown = 0
    rows = store.state_changes_for_provenance()

    for row in rows:
        result: ProvenanceResult = classify(
            entity_id=row.entity_id,
            changed_at=row.changed_at,
            context_id=row.context_id,
            context_parent_id=row.context_parent_id,
            chain=chain,
            correlator=correlator,
        )
        if result.kind == "unknown":
            unknown += 1
            continue
        if result.layer == 1:
            layer1 += 1
        else:
            layer2 += 1
        if result.source_entity_id:
            by_source[result.source_entity_id] += 1

    return ProvenanceReport(
        total_state_changes=len(rows),
        automation_count=layer1 + layer2,
        layer1_count=layer1,
        layer2_count=layer2,
        unknown_count=unknown,
        by_source_entity=by_source,
    )


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total else "n/a"


def format_report(report: ProvenanceReport) -> str:
    total = report.total_state_changes

    def pct(n: int) -> str:
        return _pct(n, total)

    lines = [
        "Provenance report (Layer 1 + Layer 2 only — Layer 3 not built yet)",
        f"  state_changes classified: {total}",
        f"  automation (layer 1, context chain): {report.layer1_count} ({pct(report.layer1_count)})",
        f"  automation (layer 2, fire correlation): {report.layer2_count} ({pct(report.layer2_count)})",
        f"  unknown (abstain — not yet a human class; needs Layer 3): "
        f"{report.unknown_count} ({pct(report.unknown_count)})",
    ]
    if report.by_source_entity:
        lines.append("  top sources:")
        for entity_id, count in report.by_source_entity.most_common(20):
            lines.append(f"    {entity_id}: {count}")
    return "\n".join(lines)
