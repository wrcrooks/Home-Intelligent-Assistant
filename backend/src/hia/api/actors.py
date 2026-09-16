"""Builds the payload behind ``GET /api/provenance/actors`` — the "UI lists all
HA users alongside any user IDs seen in the event stream and asks the owner to
tag them" setup task from docs/05-provenance.md §4 Layer 3. Bridges three
sources nothing else in this codebase already combines: HA's own live user
registry, this project's own event history, and any classification the owner
has already confirmed (hia.ingest.store.EventStore.actor_classifications).
"""

from __future__ import annotations

from dataclasses import dataclass

from hia.ha.client import HomeAssistantClient
from hia.ha.registry import fetch_users
from hia.ingest.store import EventStore
from hia.provenance.actors import suggest_actor_class


@dataclass(frozen=True, slots=True)
class ActorRow:
    user_id: str
    name: str
    system_generated: bool
    event_count: int
    """How many stored events carry this user_id — 0 means this HA account has
    never (yet) caused anything this project ingested; still worth listing so
    the owner can tag it ahead of time."""
    suggested_class: str | None
    suggested_reason: str
    confirmed_class: str | None
    """The owner-confirmed classification on record, if any — what
    hia.provenance.classify actually uses. ``None`` until someone confirms one,
    regardless of how confident ``suggested_class`` is."""


async def build_actors_payload(
    client: HomeAssistantClient, store: EventStore
) -> list[ActorRow]:
    users = await fetch_users(client)
    timestamps = store.user_event_timestamps()
    confirmed = store.actor_classifications()

    rows = []
    for user in users:
        user_timestamps = timestamps.get(user.id, [])
        suggestion = suggest_actor_class(user, user_timestamps)
        rows.append(
            ActorRow(
                user_id=user.id,
                name=user.name,
                system_generated=user.system_generated,
                event_count=len(user_timestamps),
                suggested_class=suggestion.suggested_class,
                suggested_reason=suggestion.reason,
                confirmed_class=confirmed.get(user.id),
            )
        )

    # A user_id seen in the event stream that isn't in HA's own user registry
    # at all — a stale/deleted account, or the registry fetch degraded (e.g. a
    # non-admin token, hia.ha.registry.fetch_users) — is flagged rather than
    # silently dropped, since it's still an unclassified source of events.
    known_ids = {u.id for u in users}
    for user_id, user_timestamps in timestamps.items():
        if user_id in known_ids:
            continue
        rows.append(
            ActorRow(
                user_id=user_id,
                name=f"(unknown account: {user_id})",
                system_generated=False,
                event_count=len(user_timestamps),
                suggested_class=None,
                suggested_reason="user_id not found in Home Assistant's own user registry",
                confirmed_class=confirmed.get(user_id),
            )
        )

    return rows
