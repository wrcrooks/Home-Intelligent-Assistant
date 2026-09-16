"""hia.provenance.classify: combines Layer 1, Layer 2 and Layer 3, and — the
deliberate scoping decisions this module makes — abstains (`unknown`) rather
than ever guessing at a class it cannot back up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hia.ingest.store import ProvenanceEvent
from hia.provenance.chain import ContextChainResolver
from hia.provenance.classify import classify
from hia.provenance.correlate import AutomationCorrelator, automation_firings

_T = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)


def _automation_triggered(user_id: str | None = None) -> ProvenanceEvent:
    return ProvenanceEvent(
        event_type="automation_triggered",
        time_fired=_T,
        context_id="ctx-automation",
        context_parent_id=None,
        context_user_id=user_id,
        data={"entity_id": "automation.a"},
    )


def test_automation_triggered_with_no_user_is_automation_ha() -> None:
    """The common case: HA's automation engine fired on its own conditions, no
    person attached at all."""
    events = [_automation_triggered()]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
        actor_classifications={},
    )

    assert result.kind == "automation_ha"
    assert result.layer == 1
    assert result.source_entity_id == "automation.a"


def test_a_confirmed_human_actor_on_the_origin_is_human_ui() -> None:
    """A person clicking "run automation now" (or, more commonly, a bare
    call_service from the app) carries *their own* user_id on the originating
    event -- Layer 3 resolves that to human_ui once the owner has confirmed the
    account is a real person."""
    events = [_automation_triggered(user_id="user-will")]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
        actor_classifications={"user-will": "human"},
    )

    assert result.kind == "human_ui"
    assert result.layer == 1


def test_a_confirmed_voice_bridge_actor_is_human_voice() -> None:
    events = [_automation_triggered(user_id="user-alexa")]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
        actor_classifications={"user-alexa": "voice_bridge"},
    )

    assert result.kind == "human_voice"


def test_a_confirmed_service_account_actor_is_automation_external() -> None:
    """The Node-RED trap docs/05-provenance.md §3 warns about: a user_id is
    present, but it belongs to a confirmed automation tool, not a person."""
    events = [_automation_triggered(user_id="user-nodered")]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
        actor_classifications={"user-nodered": "service_account"},
    )

    assert result.kind == "automation_external"


def test_an_unclassified_actor_on_the_origin_abstains() -> None:
    """A real user_id, but this household hasn't confirmed who the account is
    yet -- CLAUDE.md's abstain-rather-than-guess non-negotiable applies exactly
    here, not just to the no-context fallthrough."""
    events = [_automation_triggered(user_id="user-mystery")]
    chain = ContextChainResolver(events)
    correlator = AutomationCorrelator(automation_firings(events), {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-x",
        context_parent_id="ctx-automation",
        chain=chain,
        correlator=correlator,
        actor_classifications={},
    )

    assert result.kind == "unknown"


def test_layer2_correlation_is_always_automation_ha() -> None:
    """The sun-trigger case: no context at all, so Layer 1 can't even start --
    and by construction (docs/05-provenance.md §3), a Layer 2 match never has an
    actor to look up, so it's always automation_ha regardless of
    actor_classifications' contents."""
    events = [_automation_triggered()]  # no user_id: this IS what a sun trigger looks like
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
        actor_classifications={},
    )

    assert result.kind == "automation_ha"
    assert result.layer == 2
    assert result.source_entity_id == "automation.a"


def test_abstains_rather_than_guessing_when_nothing_explains_it() -> None:
    """CLAUDE.md non-negotiable #4: abstain rather than guess. A row with a
    context nothing recorded explains, and no correlating firing, must come back
    `unknown` -- this is also the human_physical/device_local ambiguity
    classify.py's own docstring explains, deliberately left unresolved here."""
    chain = ContextChainResolver([])
    correlator = AutomationCorrelator([], {})

    result = classify(
        entity_id="light.x",
        changed_at=_T,
        context_id="ctx-unexplained",
        context_parent_id=None,
        chain=chain,
        correlator=correlator,
        actor_classifications={},
    )

    assert result.kind == "unknown"
    assert result.layer is None
    assert result.source_entity_id is None
