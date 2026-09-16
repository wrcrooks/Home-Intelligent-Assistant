"""Combines Layer 1 (:mod:`hia.provenance.chain`) and Layer 2
(:mod:`hia.provenance.correlate`) into a per-row classification.

**This slice deliberately stops at a binary output: ``automation`` or
``unknown``.** The full taxonomy in docs/05-provenance.md §2
(``human_physical``/``human_ui``/``human_voice``/``automation_ha``/
``automation_external``/``device_local``/``hia_self``) needs Layer 3 — per-actor
classification of every observed ``user_id`` as human, voice bridge, or service
account — which is not built yet (docs/04-roadmap.md P3 is split into this slice
and a second one for Layer 3 + admission control). Without Layer 3, a row that
Layer 1 and Layer 2 both fail to explain might be a human action, or might be an
external automation (Node-RED, AppDaemon) whose service calls carry a `user_id`
and look human by context alone (docs/05-provenance.md §3) — this classifier has
no way yet to tell those apart.

Per CLAUDE.md's non-negotiable #4, "abstain rather than guess": rather than
default that fallthrough to ``human`` (which would be a guess this slice cannot
back up, and a dangerous one — it's exactly the Node-RED trap), it stays
``unknown``. This is a safe, conservative default for reward gating even before
Layer 3 exists: nothing is ever misclassified as human evidence, and Layer 3, once
built, only ever *upgrades* some `unknown`s to a real class — it never has to
walk back a wrong ``automation`` classification `unknown` produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from hia.provenance.chain import ContextChainResolver
from hia.provenance.correlate import AutomationCorrelator

ProvenanceKind = Literal["automation", "unknown"]


@dataclass(frozen=True, slots=True)
class ProvenanceResult:
    kind: ProvenanceKind
    layer: Literal[1, 2] | None
    """Which layer produced this classification — ``None`` for ``unknown``."""
    source_entity_id: str | None
    """The automation/script/call_service responsible, when known."""


_UNKNOWN = ProvenanceResult(kind="unknown", layer=None, source_entity_id=None)


def classify(
    *,
    entity_id: str,
    changed_at: datetime | None,
    context_id: str | None,
    context_parent_id: str | None,
    chain: ContextChainResolver,
    correlator: AutomationCorrelator,
) -> ProvenanceResult:
    """Classify one state change. Layer 1 (context) is tried first — it's exact
    when it fires, so there's no reason to also run the timing-based Layer 2 when
    Layer 1 already has an answer."""
    origin = chain.resolve(context_id, context_parent_id)
    if origin is not None:
        return ProvenanceResult(kind="automation", layer=1, source_entity_id=origin.entity_id)

    if changed_at is not None:
        firing = correlator.correlate(entity_id, changed_at)
        if firing is not None:
            return ProvenanceResult(
                kind="automation", layer=2, source_entity_id=firing.automation_entity_id
            )

    return _UNKNOWN
