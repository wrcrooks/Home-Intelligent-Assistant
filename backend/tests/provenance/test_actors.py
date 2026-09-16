"""hia.provenance.actors: Layer 3's suggestion heuristics. Nothing here ever
writes a classification -- see the module's own docstring on why suggestions
are never automatic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hia.ha.models import UserRegistryEntry
from hia.provenance.actors import (
    REGULARITY_SUSPICIOUS_BELOW,
    interval_regularity,
    suggest_actor_class,
    temporal_regularity,
)


def _user(name: str, *, system_generated: bool = False) -> UserRegistryEntry:
    return UserRegistryEntry(id="user-1", name=name, system_generated=system_generated)


def test_system_generated_flag_suggests_service_account() -> None:
    suggestion = suggest_actor_class(_user("Supervisor", system_generated=True), [])
    assert suggestion.suggested_class == "service_account"


def test_automation_tool_name_suggests_service_account() -> None:
    for name in ("Node-RED", "AppDaemon", "n8n"):
        suggestion = suggest_actor_class(_user(name), [])
        assert suggestion.suggested_class == "service_account", name


def test_voice_bridge_name_suggests_voice_bridge() -> None:
    for name in ("Alexa", "Google Assistant", "HomeKit"):
        suggestion = suggest_actor_class(_user(name), [])
        assert suggestion.suggested_class == "voice_bridge", name


def test_an_ordinary_persons_name_suggests_nothing() -> None:
    """No heuristic having an opinion is itself the correct signal for a real
    person's own account -- see the module docstring."""
    suggestion = suggest_actor_class(_user("Will"), [])
    assert suggestion.suggested_class is None


def test_sharply_peaked_timing_with_a_constant_interval_suggests_service_account() -> None:
    """The docs/05-provenance.md §4 example almost verbatim: the same action at
    17:30:00, every day -- both peaked time-of-day *and* a perfectly constant
    (24h) interval between firings, exactly what a real automation looks like
    on both signals at once."""
    base = datetime(2026, 9, 1, 17, 30, 0, tzinfo=UTC)
    timestamps = [base + timedelta(days=i) for i in range(20)]  # all at hour 17

    suggestion = suggest_actor_class(_user("Mystery Account"), timestamps)

    assert suggestion.suggested_class == "service_account"
    assert "regularity" in suggestion.reason


def test_diffuse_timing_suggests_nothing() -> None:
    base = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
    # Spread evenly across all 24 hours -- maximally diffuse, the human case.
    timestamps = [base + timedelta(hours=i) for i in range(48)]

    suggestion = suggest_actor_class(_user("Will"), timestamps)

    assert suggestion.suggested_class is None


def test_peaked_time_of_day_but_irregular_intervals_suggests_nothing() -> None:
    """A real, live-found false positive, not a hypothetical: a real person's
    own account produced a peaked time-of-day distribution (two concentrated
    testing sessions, both around the same hour on different days) that
    temporal_regularity alone read as suspiciously regular. The gaps between
    those real clicks, though, ranged from milliseconds to tens of seconds --
    nothing like a real automation's near-constant firing interval -- and
    that's what must save it from a false suggestion here."""
    day1 = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    day2 = datetime(2026, 9, 16, 12, 5, 0, tzinfo=UTC)
    # Irregular gaps within each cluster: sub-second bursts and multi-second
    # pauses, mirroring the real click pattern that was actually observed.
    offsets = [0, 0.05, 0.3, 1.2, 8.4, 20.1, 45.0, 120.0]
    timestamps = [day1 + timedelta(seconds=o) for o in offsets] + [
        day2 + timedelta(seconds=o) for o in offsets
    ]

    suggestion = suggest_actor_class(_user("Will"), timestamps)

    assert suggestion.suggested_class is None


def test_temporal_regularity_returns_none_with_too_little_data() -> None:
    assert temporal_regularity([datetime(2026, 9, 1, tzinfo=UTC)] * 4) is None


def test_temporal_regularity_is_low_for_a_single_hour_and_high_for_uniform() -> None:
    peaked = [datetime(2026, 9, 1, 17, 0, 0, tzinfo=UTC) + timedelta(days=i) for i in range(10)]
    uniform = [datetime(2026, 9, 1, i, 0, 0, tzinfo=UTC) for i in range(24)]

    peaked_score = temporal_regularity(peaked)
    uniform_score = temporal_regularity(uniform)

    assert peaked_score is not None
    assert uniform_score is not None
    assert peaked_score < REGULARITY_SUSPICIOUS_BELOW
    assert uniform_score > REGULARITY_SUSPICIOUS_BELOW
    assert peaked_score < uniform_score


def test_interval_regularity_returns_none_with_too_little_data() -> None:
    assert interval_regularity([datetime(2026, 9, 1, tzinfo=UTC)] * 5) is None


def test_interval_regularity_is_low_for_constant_and_high_for_varied_gaps() -> None:
    base = datetime(2026, 9, 1, tzinfo=UTC)
    constant = [base + timedelta(minutes=5 * i) for i in range(10)]  # every gap = 300s
    varied = [
        base,
        base + timedelta(seconds=0.5),
        base + timedelta(seconds=3),
        base + timedelta(seconds=40),
        base + timedelta(minutes=8),
        base + timedelta(hours=2),
        base + timedelta(hours=2, minutes=1),
    ]

    constant_score = interval_regularity(constant)
    varied_score = interval_regularity(varied)

    assert constant_score is not None
    assert varied_score is not None
    assert constant_score < REGULARITY_SUSPICIOUS_BELOW
    assert varied_score > REGULARITY_SUSPICIOUS_BELOW
