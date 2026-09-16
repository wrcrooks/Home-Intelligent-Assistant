"""Layer 3 of the provenance classifier: per-actor classification
(docs/05-provenance.md §4).

Every HA user account a context's ``user_id`` can point to is classified once as
one of three things:

- ``human`` — a real person, acting through the app, a dashboard, or a physical
  device tied to their account.
- ``voice_bridge`` — Assist, Alexa, Google Home, HomeKit and similar bridges that
  authenticate as a (non-human) HA user but represent a genuine spoken human
  request (``human_voice`` in the full taxonomy — still evidence).
- ``service_account`` — Node-RED, AppDaemon, n8n and similar automation tools
  that also authenticate as an HA user, but whose actions are machine-caused
  (``automation_external`` in the full taxonomy — never evidence). This is the
  trap docs/05-provenance.md §3 and CLAUDE.md's traps section both call out: "has
  a user_id, therefore human" is backwards for exactly these accounts.

**Classification here is never automatic.** Per docs/05-provenance.md §4: "The
regularity test is a suggestion, never an automatic classification." This module
only ever *suggests* — :func:`suggest_actor_class` — and the caller (the API/UI)
is responsible for having an owner confirm a suggestion before
:meth:`hia.ingest.store.EventStore.set_actor_classification` ever gets called.
There is no code path in this module that writes a classification on its own.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from hia.ha.models import UserRegistryEntry

ActorClass = Literal["human", "voice_bridge", "service_account"]

# docs/05-provenance.md §4: "Auto-suggestions come from HA's system_generated
# flag, name matching (node-red, appdaemon, homekit, alexa, google, n8n)".
# Split by what each tool actually is, not left as one undifferentiated bucket:
# the voice/smart-home bridges represent a real spoken human request (evidence),
# the automation tools do not.
_SERVICE_ACCOUNT_NAME_HINTS = ("node-red", "node red", "appdaemon", "n8n")
_VOICE_BRIDGE_NAME_HINTS = ("homekit", "alexa", "google", "assist")


@dataclass(frozen=True, slots=True)
class ActorSuggestion:
    suggested_class: ActorClass | None
    """None when nothing here has an opinion -- a real person's own account,
    for instance, matches none of these heuristics, which is itself correct:
    the absence of an automation-tool signature is exactly what a human account
    looks like."""
    reason: str


def suggest_actor_class(
    user: UserRegistryEntry, event_timestamps: list[datetime]
) -> ActorSuggestion:
    """Combines the three heuristics from docs/05-provenance.md §4, strongest
    first. Returns the first one with an opinion rather than trying to merge
    conflicting signals -- ``system_generated`` and name matching are both
    fairly reliable; regularity is explicitly the weakest ("a shift worker's
    routine can look mechanical") and is only reached when neither of the
    others has anything to say."""
    if user.system_generated:
        return ActorSuggestion("service_account", "system_generated flag is set")

    name = user.name.lower()
    for hint in _SERVICE_ACCOUNT_NAME_HINTS:
        if hint in name:
            return ActorSuggestion("service_account", f"name matches known automation tool {hint!r}")
    for hint in _VOICE_BRIDGE_NAME_HINTS:
        if hint in name:
            return ActorSuggestion("voice_bridge", f"name matches known voice/smart-home bridge {hint!r}")

    time_of_day = temporal_regularity(event_timestamps)
    intervals = interval_regularity(event_timestamps)
    # Both signals must independently look automation-like before this suggests
    # anything -- found necessary against real data, not assumed: the owner's
    # own account, a real human, produced a peaked time-of-day distribution on
    # its own (concentrated testing sessions around the same hour on two
    # different days scored 0.44, just under the old single-signal threshold)
    # that time-of-day entropy alone could not tell apart from an automation.
    # What *did* tell them apart was interval entropy -- the owner's actual
    # clicks landed anywhere from milliseconds to tens of seconds apart within
    # a session, nothing like a real automation's near-constant firing
    # interval. Requiring both lines up with docs/05-provenance.md §4's own
    # framing of this as the weakest of the three heuristics: one peaked signal
    # has too many innocent explanations (a fixed routine, a concentrated
    # testing session, a shift worker) to suggest anything on its own.
    if (
        time_of_day is not None
        and time_of_day < REGULARITY_SUSPICIOUS_BELOW
        and intervals is not None
        and intervals < REGULARITY_SUSPICIOUS_BELOW
    ):
        return ActorSuggestion(
            "service_account",
            f"low regularity entropy (time-of-day={time_of_day:.2f}, "
            f"intervals={intervals:.2f}) -- suspiciously machine-like timing on "
            "both signals, but this is still a weak signal (a shift worker can "
            "look mechanical too); review before confirming",
        )

    return ActorSuggestion(None, "no heuristic matched -- looks like a real person")


REGULARITY_SUSPICIOUS_BELOW = 0.5
"""Shared threshold (0-1 normalized Shannon entropy) for both
:func:`temporal_regularity` and :func:`interval_regularity` — an actor's
actions have to cluster tightly enough on *both* signals to look
automation-like before :func:`suggest_actor_class` suggests anything. Picked to
be conservative (only flags genuinely peaked distributions) since
docs/05-provenance.md §4 is explicit this heuristic is a suggestion, not a
verdict — and confirmed live that time-of-day alone at this same threshold
still false-positives on a real, if unusually concentrated, human account."""

_TIME_OF_DAY_BUCKETS = 24
"""One bucket per hour -- coarse enough that a human's normal daily variation
doesn't get flagged, fine enough to catch "fires at 17:30:00 every day"."""


def temporal_regularity(timestamps: list[datetime]) -> float | None:
    """Normalized Shannon entropy of an actor's time-of-day distribution — the
    first of the two signals docs/05-provenance.md §4's regularity heuristic
    describes ("inter-event intervals *and* time-of-day distribution"; the
    second is :func:`interval_regularity`). 1.0 = perfectly uniform across the
    day (very human). 0.0 = every single action at the exact same hour (very
    automation). ``None`` when there isn't enough data (fewer than 5 events) to
    say anything responsible.

    **On its own this is not enough to suggest anything** — confirmed against
    real data, not assumed: a real person's account can legitimately produce a
    peaked distribution (a concentrated testing session, a fixed daily routine,
    a shift worker) that looks identical to an automation by this signal alone.
    See :func:`suggest_actor_class`, which requires this *and*
    :func:`interval_regularity` to agree before suggesting ``service_account``.
    """
    if len(timestamps) < 5:
        return None

    hour_counts = Counter(ts.hour for ts in timestamps)
    total = len(timestamps)
    entropy = -sum(
        (count / total) * math.log2(count / total) for count in hour_counts.values()
    )
    max_entropy = math.log2(_TIME_OF_DAY_BUCKETS)
    return entropy / max_entropy


_INTERVAL_BUCKET_EDGES_SECONDS = (1, 5, 30, 120, 600, 3600, 14400)
"""Upper edge of each interval bucket, log-scaled rather than linear: an
automation firing "every 5 minutes" and one firing "every 5 minutes ± jitter"
both need to land in the same bucket, and a human's real gaps span many orders
of magnitude in one session (sub-second UI double-fires up to multi-hour idle)
— linear buckets would need unreasonable resolution to tell those apart."""


def interval_regularity(timestamps: list[datetime]) -> float | None:
    """Normalized Shannon entropy of an actor's inter-event gaps, bucketed on a
    log scale (see :data:`_INTERVAL_BUCKET_EDGES_SECONDS`) — the second signal
    docs/05-provenance.md §4 describes. 1.0 = gaps spread evenly across every
    order of magnitude (very human: bursts of rapid clicks, then long pauses).
    0.0 = every gap lands in the same bucket (very automation: a near-constant
    firing interval). ``None`` when there are fewer than 5 gaps (6 events) to
    compute from.

    Found necessary, not just theoretically motivated: the owner's own real
    account, tested live against this project's soak-test data, had a peaked
    time-of-day distribution (concentrated testing sessions) that
    :func:`temporal_regularity` alone read as suspiciously regular. This signal
    correctly told it apart — real clicks during a testing session land
    anywhere from milliseconds to tens of seconds apart, nothing like a real
    automation's near-constant interval."""
    if len(timestamps) < 6:
        return None

    sorted_ts = sorted(timestamps)
    gaps = [
        (b - a).total_seconds() for a, b in zip(sorted_ts, sorted_ts[1:], strict=False)
    ]
    bucket_counts = Counter(_interval_bucket(gap) for gap in gaps)
    total = len(gaps)
    entropy = -sum(
        (count / total) * math.log2(count / total) for count in bucket_counts.values()
    )
    max_entropy = math.log2(len(_INTERVAL_BUCKET_EDGES_SECONDS) + 1)
    return entropy / max_entropy


def _interval_bucket(gap_seconds: float) -> int:
    for i, edge in enumerate(_INTERVAL_BUCKET_EDGES_SECONDS):
        if gap_seconds < edge:
            return i
    return len(_INTERVAL_BUCKET_EDGES_SECONDS)
