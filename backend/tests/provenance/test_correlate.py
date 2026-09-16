"""hia.provenance.correlate: Layer 2, automation-fire correlation — the layer
that closes the sun/time-trigger hole Layer 1 cannot see at all, since those
triggers set no context."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hia.ingest.store import ProvenanceEvent
from hia.provenance.correlate import AutomationCorrelator, automation_firings

_T = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)


def _firing_event(entity_id: str, context_id: str = "ctx-fire") -> ProvenanceEvent:
    return ProvenanceEvent(
        event_type="automation_triggered",
        time_fired=_T,
        context_id=context_id,
        context_parent_id=None,
        context_user_id=None,
        data={"entity_id": entity_id},
    )


def test_correlates_a_sun_triggered_firing_with_no_context() -> None:
    """The exact scenario Layer 1 cannot handle: a sunset trigger fires with no
    context (docs/05-provenance.md §3), but automation_triggered still fires, and
    its known target (extracted from its stored config by hia.ha.automations)
    changes state shortly after."""
    firings = automation_firings([_firing_event("automation.porch_at_sunset")])
    correlator = AutomationCorrelator(
        firings, {"automation.porch_at_sunset": frozenset({"light.porch"})}
    )

    result = correlator.correlate("light.porch", _T + timedelta(seconds=2))

    assert result is not None
    assert result.automation_entity_id == "automation.porch_at_sunset"


def test_outside_the_window_does_not_correlate() -> None:
    firings = automation_firings([_firing_event("automation.porch_at_sunset")])
    correlator = AutomationCorrelator(
        firings,
        {"automation.porch_at_sunset": frozenset({"light.porch"})},
        window=timedelta(seconds=5),
    )

    assert correlator.correlate("light.porch", _T + timedelta(seconds=10)) is None


def test_entity_not_in_any_known_target_set_does_not_correlate() -> None:
    firings = automation_firings([_firing_event("automation.porch_at_sunset")])
    correlator = AutomationCorrelator(
        firings, {"automation.porch_at_sunset": frozenset({"light.porch"})}
    )

    assert correlator.correlate("light.kitchen", _T + timedelta(seconds=1)) is None


def test_picks_the_nearest_prior_firing_when_several_exist() -> None:
    firings = automation_firings(
        [
            ProvenanceEvent(
                event_type="automation_triggered",
                time_fired=_T,
                context_id="ctx-1",
                context_parent_id=None,
                context_user_id=None,
                data={"entity_id": "automation.a"},
            ),
            ProvenanceEvent(
                event_type="automation_triggered",
                time_fired=_T + timedelta(seconds=3),
                context_id="ctx-2",
                context_parent_id=None,
                context_user_id=None,
                data={"entity_id": "automation.a"},
            ),
        ]
    )
    correlator = AutomationCorrelator(firings, {"automation.a": frozenset({"light.x"})})

    result = correlator.correlate("light.x", _T + timedelta(seconds=4))

    assert result is not None
    assert result.context_id == "ctx-2"


def test_a_change_before_any_firing_does_not_correlate() -> None:
    firings = automation_firings([_firing_event("automation.a")])
    correlator = AutomationCorrelator(firings, {"automation.a": frozenset({"light.x"})})

    assert correlator.correlate("light.x", _T - timedelta(seconds=1)) is None
