"""Layer 1 of the provenance classifier: context-chain resolution
(docs/05-provenance.md §4).

Home Assistant normally propagates a triggering automation's context into the
``parent_id`` of everything it causes. Two real, live-verified shapes of that
propagation exist, and this resolver has to handle both:

- **Same-id sharing** — a service call and the state change it directly causes can
  share the *same* ``context_id`` rather than a parent/child pair (confirmed live
  in P1: a REST-triggered ``call_service`` event and the ``state_changed`` it
  caused had identical context ids — see docs/HANDOFF.md).
- **Parent-id chaining** — an automation fires with context ``A``; whatever it
  causes carries ``parent_id = A`` (docs/05-provenance.md §3).

So resolving a row's context means checking both its own ``context_id`` and its
``context_parent_id`` against every recorded machine event, then climbing further
through *that* event's own ``context_parent_id`` in case it is itself nested inside
another (a service call inside an automation inside a script, say) — bounded by
``max_depth`` and guarded against cycles, per docs/05-provenance.md §4's own
description of this layer.

**What this layer cannot see**: Sun and Time-of-Day triggers set no context at all
(docs/05-provenance.md §3) — there is no chain to walk. That hole is Layer 2's job
(:mod:`hia.provenance.correlate`), not this module's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from hia.ingest.store import ProvenanceEvent

ChainKind = Literal["automation", "script", "call_service"]


@dataclass(frozen=True, slots=True)
class ChainOrigin:
    """The machine event a context chain was resolved to."""

    kind: ChainKind
    entity_id: str | None
    """The automation/script's own entity_id, when the origin event carried one
    (automation_triggered and script_started always do; a bare call_service does
    not, since the ``data`` payload there is domain/service/service_data, not an
    entity_id of its own)."""
    depth: int
    """How many context hops were climbed to reach this origin. 0 means the
    classified row's own context_id (or its immediate parent) matched directly."""


def _as_origin(event: ProvenanceEvent, depth: int) -> ChainOrigin:
    kind: ChainKind = (
        "automation"
        if event.event_type == "automation_triggered"
        else "script"
        if event.event_type == "script_started"
        else "call_service"
    )
    entity_id = event.data.get("entity_id")
    return ChainOrigin(
        kind=kind, entity_id=entity_id if isinstance(entity_id, str) else None, depth=depth
    )


class ContextChainResolver:
    """Built once per classification run from every currently-stored
    ``automation_triggered``/``script_started``/``call_service`` event — cheap in
    practice, since these are a small fraction of total ingested volume, and
    avoids re-querying the store per row classified."""

    def __init__(self, events: Iterable[ProvenanceEvent], *, max_depth: int = 10) -> None:
        self._by_context: dict[str, ProvenanceEvent] = {}
        for event in events:
            # Last write wins: context_id should be unique per event by
            # construction (it's how HA itself identifies one), so a collision
            # would mean something worth investigating, not silently dropping one.
            self._by_context[event.context_id] = event
        self._max_depth = max_depth

    def resolve(self, context_id: str | None, context_parent_id: str | None) -> ChainOrigin | None:
        """Resolve a row's context to the most distant machine ancestor this
        resolver can find, or ``None`` if the chain never touches a recorded
        machine event at all (the row is a Layer 2 or abstain candidate)."""
        seen: set[str] = set()
        queue: list[str] = [i for i in (context_id, context_parent_id) if i is not None]
        best: ChainOrigin | None = None
        depth = 0

        while queue and depth <= self._max_depth:
            candidate = queue.pop(0)
            if candidate in seen:
                continue
            seen.add(candidate)
            event = self._by_context.get(candidate)
            if event is None:
                continue
            best = _as_origin(event, depth)
            if event.context_parent_id is not None:
                queue.append(event.context_parent_id)
            depth += 1

        return best
