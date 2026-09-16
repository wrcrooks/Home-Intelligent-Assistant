"""hia.provenance.classify: combines Layer 1 and Layer 2, and — the deliberate
scoping decision this slice makes — abstains (`unknown`) rather than ever
guessing `human` for anything neither layer explains."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hia.ingest.store import ProvenanceEvent
from hia.provenance.chain import ContextChainResolver
from hia.provenance.classify import classify
from hia.provenance.correlate import AutomationCorrelator, automation_firings

_T = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)


def test_layer1_wins_when_context_resolves() -> None:
    events = [
        ProvenanceEvent(
            "automation_triggered", _T, "ctx-automation", None, {"entity_id": "automation.a"}
        )
    ]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
    )

    assert result.kind == "automation"
    assert result.layer == 1
    assert result.source_entity_id == "automation.a"


def test_layer2_used_when_layer1_has_nothing_to_chain_from() -> None:
    """The sun-trigger case: no context at all, so Layer 1 can't even start."""
    events = [
        ProvenanceEvent(
            "automation_triggered", _T, "ctx-automation", None, {"entity_id": "automation.a"}
        )
    ]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(
        automation_firings(events), {"automation.a": frozenset({"light.x"})}
    )

    result = classify(
        entity_id="light.x",
        changed_at=_T + timedelta(seconds=1),
        context_id=None,
        context_parent_id=None,
        chain=chain,
        correlator=correlator,
    )

    assert result.kind == "automation"
    assert result.layer == 2
    assert result.source_entity_id == "automation.a"


def test_abstains_rather_than_guessing_human_when_neither_layer_explains_it() -> None:
    """CLAUDE.md non-negotiable #4: abstain rather than guess. A row with a
    context nothing recorded explains, and no correlating firing, must come back
    `unknown` — never a claimed `human` this slice has no Layer 3 to back up."""
    chain = ContextChainResolver([])
    correlator = AutomationCorrelator([], {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-unexplained",
        context_parent_id=None,
        chain=chain,
        correlator=correlator,
    )

    assert result.kind == "unknown"
    assert result.layer is None
    assert result.source_entity_id is None
