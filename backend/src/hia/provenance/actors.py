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

    regularity = temporal_regularity(event_timestamps)
    if regularity is not None and regularity < REGULARITY_SUSPICIOUS_BELOW:
        return ActorSuggestion(
            "service_account",
            f"low temporal-regularity entropy ({regularity:.2f}) -- "
            "suspiciously machine-like timing, but this is a weak signal on its "
            "own (a shift worker can look mechanical too); review before confirming",
        )

    return ActorSuggestion(None, "no heuristic matched -- looks like a real person")


REGULARITY_SUSPICIOUS_BELOW = 0.5
"""Normalized Shannon entropy (0-1) of an actor's time-of-day distribution.
Below this, an actor's actions cluster tightly enough into a handful of
time-of-day buckets to look automation-like -- picked to be conservative (only
flags genuinely peaked distributions) since docs/05-provenance.md §4 is explicit
this heuristic is a suggestion, not a verdict."""

_TIME_OF_DAY_BUCKETS = 24
"""One bucket per hour -- coarse enough that a human's normal daily variation
doesn't get flagged, fine enough to catch "fires at 17:30:00 every day"."""


def temporal_regularity(timestamps: list[datetime]) -> float | None:
    """Normalized Shannon entropy of an actor's time-of-day distribution
    (docs/05-provenance.md §4's regularity heuristic uses inter-event intervals
    *and* time-of-day; this implements the time-of-day half, which needs no
    minimum gap between consecutive events to be meaningful and degrades
    gracefully with sparse data). 1.0 = perfectly uniform across the day (very
    human). 0.0 = every single action at the exact same hour (very automation).
    ``None`` when there isn't enough data (fewer than 5 events) to say anything
    responsible."""
    if len(timestamps) < 5:
        return None

    hour_counts = Counter(ts.hour for ts in timestamps)
    total = len(timestamps)
    entropy = -sum(
        (count / total) * math.log2(count / total) for count in hour_counts.values()
    )
    max_entropy = math.log2(_TIME_OF_DAY_BUCKETS)
    return entropy / max_entropy
