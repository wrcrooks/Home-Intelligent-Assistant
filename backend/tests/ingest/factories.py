"""Builds typed WatchedEvent fixtures for the ingest tests, without going through a
real (or fake) websocket connection — these tests are about what the store and
pipeline do with an event, not about how it arrived.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hia.ha.models import Context, HAEvent, State, WatchedEvent

_NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def make_state_changed_watched(
    seq: int,
    entity_id: str,
    *,
    new_state: str | None = "on",
    old_state: str | None = None,
    resumed_after_gap: bool = False,
    context_user_id: str | None = None,
    context_parent_id: str | None = None,
) -> WatchedEvent:
    new = (
        State(
            entity_id=entity_id,
            state=new_state,
            attributes={"friendly_name": entity_id},
            last_changed=_NOW,
            last_updated=_NOW,
            context=Context(id="ctx-new"),
        )
        if new_state is not None
        else None
    )
    old = (
        State(
            entity_id=entity_id,
            state=old_state,
            attributes={},
            last_changed=_NOW,
            last_updated=_NOW,
            context=Context(id="ctx-old"),
        )
        if old_state is not None
        else None
    )
    event = HAEvent(
        event_type="state_changed",
        data={
            "entity_id": entity_id,
            "old_state": old.model_dump(mode="json") if old else None,
            "new_state": new.model_dump(mode="json") if new else None,
        },
        origin="LOCAL",
        time_fired=_NOW,
        context=Context(id="ctx-1", parent_id=context_parent_id, user_id=context_user_id),
    )
    return WatchedEvent(seq=seq, resumed_after_gap=resumed_after_gap, event=event)


def make_generic_watched(
    seq: int,
    event_type: str,
    data: dict[str, object],
    *,
    resumed_after_gap: bool = False,
) -> WatchedEvent:
    event = HAEvent(
        event_type=event_type,
        data=data,
        origin="LOCAL",
        time_fired=_NOW,
        context=Context(id="ctx-2"),
    )
    return WatchedEvent(seq=seq, resumed_after_gap=resumed_after_gap, event=event)
