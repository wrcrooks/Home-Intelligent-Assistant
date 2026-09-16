"""Combines Layer 1 (:mod:`hia.provenance.chain`), Layer 2
(:mod:`hia.provenance.correlate`) and Layer 3 (:mod:`hia.provenance.actors`)
into a per-row classification.

**Produces a subset of the full taxonomy in docs/05-provenance.md §2** —
``human_ui``, ``human_voice``, ``automation_ha``, ``automation_external``, and
``unknown``. Two classes are deliberately still out of reach here:

- ``human_physical`` and ``device_local`` both present identically: a row with
  no context at all, that Layer 2 also fails to correlate to any automation
  firing. Per docs/05-provenance.md §3, that's *expected* — a genuine wall-switch
  press looks exactly like a sun-triggered automation Layer 2 didn't catch.
  Docs are explicit that ``device_local`` "cannot be detected automatically at
  all" and needs a *separate*, not-yet-built, per-entity opt-in list — Layer 3's
  per-*actor* classification is the wrong tool for a change that has no actor at
  all. Rather than default that fallthrough to ``human_physical`` (a guess this
  slice cannot back up, and one that would silently admit real device_local
  contamination into the reward signal — CLAUDE.md's non-negotiable #3 treats
  that as a fatal, self-reinforcing-runaway risk, not a nicety), it stays
  ``unknown``. Building the per-entity device_local list is what upgrades this
  specific fallthrough later.
- ``hia_self`` needs the governor (P6) to actually be calling HA services first;
  nothing does yet.

Per CLAUDE.md's non-negotiable #4, "abstain rather than guess": every fallthrough
in this module — an unresolved chain, an unrecognized actor, a genuinely
context-less row — stays ``unknown`` rather than defaulting to a guess. Layer 3,
once an actor is confirmed, only ever *upgrades* an ``unknown``-shaped case to a
real class; nothing here downgrades a real class back to a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from hia.provenance.chain import ContextChainResolver
from hia.provenance.correlate import AutomationCorrelator

ProvenanceKind = Literal[
    "human_ui", "human_voice", "automation_ha", "automation_external", "unknown"
]

def _kind_for_actor_class(actor_class: str) -> ProvenanceKind | None:
    """Maps a stored (plain ``str`` — see :func:`classify`'s own note on why)
    actor class to its taxonomy kind. Anything other than the three recognized
    values — including a stray/corrupted DB row — returns ``None`` rather than
    raising, so the caller abstains instead of crashing."""
    if actor_class == "human":
        return "human_ui"
    if actor_class == "voice_bridge":
        return "human_voice"
    if actor_class == "service_account":
        return "automation_external"
    return None


@dataclass(frozen=True, slots=True)
class ProvenanceResult:
    kind: ProvenanceKind
    layer: Literal[1, 2] | None
    """Which layer produced this classification — ``None`` for ``unknown``."""
    source_entity_id: str | None
    """The automation/script responsible, when the origin is machine-triggered.
    ``None`` for anything actor-attributed (``human_ui``/``human_voice``/
    ``automation_external``) — the "source" there is a person or account, not an
    automation, and is available separately via the row's own context_user_id."""


_UNKNOWN = ProvenanceResult(kind="unknown", layer=None, source_entity_id=None)


def _classify_chain_origin(
    origin_entity_id: str | None,
    origin_context_user_id: str | None,
    layer: Literal[1, 2],
    actor_classifications: Mapping[str, str],
) -> ProvenanceResult:
    """Used by Layer 1 for whichever kind of machine event the chain resolved
    to — ``automation_triggered``/``script_started``/``call_service`` all get
    the same treatment here, since what actually matters is only whether a
    person's own user_id is attached to that origin event, not which kind it
    was. See :func:`classify`."""
    if origin_context_user_id is None:
        # No user attached to the originating event at all: HA's automation or
        # script engine fired on its own conditions, not a person.
        return ProvenanceResult(kind="automation_ha", layer=layer, source_entity_id=origin_entity_id)

    actor_class = actor_classifications.get(origin_context_user_id)
    kind = _kind_for_actor_class(actor_class) if actor_class is not None else None
    if kind is None:
        # Either this household hasn't confirmed who this account is yet, or
        # (defensively) the stored value isn't one of the three recognized
        # classes -- either way, abstain rather than assume either "human" or
        # "automation".
        return _UNKNOWN
    return ProvenanceResult(kind=kind, layer=layer, source_entity_id=origin_entity_id)


def classify(
    *,
    entity_id: str,
    changed_at: datetime | None,
    context_id: str | None,
    context_parent_id: str | None,
    chain: ContextChainResolver,
    correlator: AutomationCorrelator,
    actor_classifications: Mapping[str, str],
) -> ProvenanceResult:
    """Classify one state change. Layer 1 (context) is tried first — it's exact
    when it fires, so there's no reason to also run the timing-based Layer 2 when
    Layer 1 already has an answer. Layer 3 (``actor_classifications``, owner-
    confirmed only — hia.provenance.actors) is applied to whichever layer
    resolves a chain: an ``automation_triggered``/``script_started`` origin with
    no attached user is unambiguously ``automation_ha``; a bare ``call_service``
    (or a script a person clicked "run" on) carries *that person's* user_id on
    the origin event itself, which Layer 3 resolves to the right human/automation
    class."""
    origin = chain.resolve(context_id, context_parent_id)
    if origin is not None:
        return _classify_chain_origin(
            origin.entity_id, origin.context_user_id, 1, actor_classifications
        )

    if changed_at is not None:
        firing = correlator.correlate(entity_id, changed_at)
        if firing is not None:
            # Layer 2 only ever matches a firing whose own automation_triggered
            # event set no context at all (docs/05-provenance.md §3) -- there is
            # no user_id to look up, so this is always automation_ha by
            # construction, not something Layer 3 needs to weigh in on.
            return ProvenanceResult(
                kind="automation_ha", layer=2, source_entity_id=firing.automation_entity_id
            )

    return _UNKNOWN
