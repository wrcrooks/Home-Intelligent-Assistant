"""hia.provenance.chain: Layer 1, context-chain resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from hia.ingest.store import ProvenanceEvent
from hia.provenance.chain import ContextChainResolver

_T = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


def _event(
    event_type: str,
    context_id: str,
    parent_id: str | None,
    *,
    user_id: str | None = None,
    **data: object,
) -> ProvenanceEvent:
    return ProvenanceEvent(
        event_type=event_type,
        time_fired=_T,
        context_id=context_id,
        context_parent_id=parent_id,
        context_user_id=user_id,
        data=data,
    )


def test_resolves_via_same_context_id_sharing() -> None:
    """The pattern verified live in P1: a call_service event and the state change
    it directly caused shared the *same* context_id, not a parent/child pair."""
    events = [_event("call_service", "ctx-1", None, domain="light", service="turn_on")]
    resolver = ContextChainResolver(events)

    origin = resolver.resolve(context_id="ctx-1", context_parent_id=None)

    assert origin is not None
    assert origin.kind == "call_service"
    assert origin.depth == 0


def test_resolves_via_parent_id_chaining() -> None:
    events = [_event("automation_triggered", "ctx-automation", None, entity_id="automation.porch")]
    resolver = ContextChainResolver(events)

    origin = resolver.resolve(context_id="ctx-state-change", context_parent_id="ctx-automation")

    assert origin is not None
    assert origin.kind == "automation"
    assert origin.entity_id == "automation.porch"


def test_climbs_multiple_hops_to_find_the_outermost_automation() -> None:
    """A service call nested inside an automation resolves to the automation, not
    just the immediate call_service — docs/05-provenance.md §4 asks for a root."""
    events = [
        _event("automation_triggered", "ctx-automation", None, entity_id="automation.porch"),
        _event("call_service", "ctx-service", "ctx-automation", domain="light", service="turn_on"),
    ]
    resolver = ContextChainResolver(events)

    origin = resolver.resolve(context_id="ctx-service", context_parent_id=None)

    assert origin is not None
    assert origin.kind == "automation"
    assert origin.entity_id == "automation.porch"
    assert origin.depth == 1


def test_origin_carries_the_originating_events_own_context_user_id() -> None:
    """Layer 3 (hia.provenance.actors) needs this: a call_service fired directly
    by a person (the app, a "run script now" click) carries that person's own
    user_id on *this* event -- the signal that distinguishes it from HA's
    automation engine firing a service call on its own, which carries none."""
    events = [_event("call_service", "ctx-1", None, user_id="user-abc", domain="light", service="turn_on")]
    resolver = ContextChainResolver(events)

    origin = resolver.resolve(context_id="ctx-1", context_parent_id=None)

    assert origin is not None
    assert origin.context_user_id == "user-abc"


def test_returns_none_when_context_matches_nothing_recorded() -> None:
    """A bare wall-switch press, or a sun/time-triggered automation (no context at
    all) — Layer 1 correctly declines rather than guessing; Layer 2 or abstention
    is what handles this row next."""
    resolver = ContextChainResolver([])

    assert resolver.resolve(context_id="ctx-unknown", context_parent_id=None) is None
    assert resolver.resolve(context_id=None, context_parent_id=None) is None


def test_cycle_guarded_and_depth_capped() -> None:
    """A malformed or adversarial parent_id chain can never spin forever."""
    events = [
        _event("call_service", "ctx-a", "ctx-b", domain="x", service="y"),
        _event("call_service", "ctx-b", "ctx-a", domain="x", service="y"),
    ]
    resolver = ContextChainResolver(events, max_depth=5)

    origin = resolver.resolve(context_id="ctx-a", context_parent_id=None)

    # Doesn't hang, and still finds *a* machine origin along the cycle.
    assert origin is not None
    assert origin.kind == "call_service"
