"""Layer 2 of the provenance classifier: automation-fire correlation
(docs/05-provenance.md §4).

Closes the hole Layer 1 (:mod:`hia.provenance.chain`) cannot see: Sun and
Time-of-Day triggers set no context at all, so there is nothing to chain-walk. But
the critical observation from docs/05-provenance.md §4 still holds: even when a
trigger sets no context, **``automation_triggered`` still fires on the event bus**
carrying the automation's own entity_id. So instead of context, this layer
correlates on *timing*: if automation ``X`` fires, and entity ``L`` is one of the
entities ``X`` is capable of acting on (its "targets", extracted from its stored
config by :mod:`hia.ha.automations`), and ``L`` changes state within a short window
after ``X`` fired, the change is attributed to ``X`` regardless of what its own
context says.

This is deliberately timing-only and knows nothing about *why* an automation
fired — a coincidental human action on the same entity within the same window
would be misattributed. The window is kept short (default 5s) specifically to keep
that false-positive rate low; a wider window trades precision for a higher catch
rate, which is why it's a constructor parameter, not a constant.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from hia.ingest.store import ProvenanceEvent


@dataclass(frozen=True, slots=True)
class AutomationFiring:
    automation_entity_id: str
    time_fired: datetime
    context_id: str


def automation_firings(events: Iterable[ProvenanceEvent]) -> list[AutomationFiring]:
    """Extract every automation firing from the events a classification run was
    built from — the timing side of this layer's correlation."""
    firings = []
    for event in events:
        if event.event_type != "automation_triggered":
            continue
        entity_id = event.data.get("entity_id")
        if isinstance(entity_id, str):
            firings.append(AutomationFiring(entity_id, event.time_fired, event.context_id))
    return firings


class AutomationCorrelator:
    """Matches a state change to the nearest automation firing (within
    ``window``) that lists the changed entity among its own targets."""

    def __init__(
        self,
        firings: Iterable[AutomationFiring],
        targets: Mapping[str, frozenset[str]],
        *,
        window: timedelta = timedelta(seconds=5),
    ) -> None:
        self._window = window
        # Bucketed by target entity and time-sorted, so a lookup is a bisect
        # rather than an O(firings) scan per state change classified.
        by_target: dict[str, list[AutomationFiring]] = defaultdict(list)
        for firing in firings:
            for target_entity in targets.get(firing.automation_entity_id, frozenset()):
                by_target[target_entity].append(firing)
        for firing_list in by_target.values():
            firing_list.sort(key=lambda f: f.time_fired)
        self._by_target = by_target

    def correlate(self, entity_id: str, changed_at: datetime) -> AutomationFiring | None:
        """The latest firing at or before ``changed_at`` that lists ``entity_id``
        among its targets, if one exists within ``window`` — or ``None``."""
        candidates = self._by_target.get(entity_id)
        if not candidates:
            return None
        idx = bisect_right(candidates, changed_at, key=lambda f: f.time_fired) - 1
        if idx < 0:
            return None
        firing = candidates[idx]
        if changed_at < firing.time_fired or changed_at - firing.time_fired > self._window:
            return None
        return firing
