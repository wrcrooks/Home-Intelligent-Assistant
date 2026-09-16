"""Typed views over Home Assistant's websocket messages.

Registry models use ``extra="allow"``: the exact field set returned by
``config/*_registry/list`` is not pinned down anywhere authoritative outside the HA
source itself, and a new field appearing in a future HA release must never break
parsing here. Only the fields this project actually reads are declared; everything
else round-trips through ``model_extra`` untouched. Tighten these once validated
against a real, live instance (tracked in docs/HANDOFF.md).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class Context(BaseModel):
    """Ties an event/state change to whatever caused it.

    ``parent_id`` and ``user_id`` are the raw material for provenance
    classification (docs/05-provenance.md) — captured here, interpreted later.
    Both are frequently ``None`` even for automation-caused changes (Sun and
    Time-of-Day triggers set no context at all), which is exactly why provenance
    needs more than this one field. See docs/05-provenance.md §3.
    """

    id: str
    parent_id: str | None = None
    user_id: str | None = None


class State(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_id: str
    state: str
    attributes: dict[str, Any] = {}
    last_changed: datetime
    last_updated: datetime
    context: Context


class HAEvent(BaseModel):
    """The ``event`` payload of a websocket ``type: event`` message."""

    model_config = ConfigDict(extra="allow")

    event_type: str
    data: dict[str, Any]
    origin: str
    time_fired: datetime
    context: Context

    def as_state_changed(self) -> StateChanged | None:
        """Parse ``data`` as a state_changed payload, or None if not that type."""
        if self.event_type != "state_changed":
            return None
        return StateChanged.model_validate(self.data)


class StateChanged(BaseModel):
    entity_id: str
    old_state: State | None = None
    new_state: State | None = None


class WatchedEvent(BaseModel):
    """An :class:`HAEvent` plus the client-assigned bookkeeping around it.

    ``seq`` is a monotonic counter assigned by this client, independent of
    Home Assistant's own per-connection message ids — it survives reconnects, so a
    consumer can detect gaps by watching for skipped values. ``resumed_after_gap``
    is set on the first event delivered after a reconnect, since events that occurred
    while disconnected are unrecoverable from the live stream (only backfill from the
    recorder can fill that window — see docs/02-architecture.md).
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    resumed_after_gap: bool
    event: HAEvent


class RegistryEntry(BaseModel):
    """Common base for the five `config/*_registry/list` result rows."""

    model_config = ConfigDict(extra="allow")


class EntityRegistryEntry(RegistryEntry):
    entity_id: str
    unique_id: str | None = None
    platform: str | None = None
    name: str | None = None
    original_name: str | None = None
    area_id: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None
    labels: list[str] = []


class DeviceRegistryEntry(RegistryEntry):
    id: str
    name: str | None = None
    name_by_user: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    labels: list[str] = []


class AreaRegistryEntry(RegistryEntry):
    area_id: str
    name: str
    floor_id: str | None = None
    labels: list[str] = []


class FloorRegistryEntry(RegistryEntry):
    floor_id: str
    name: str
    level: int | None = None


class LabelRegistryEntry(RegistryEntry):
    label_id: str
    name: str
    color: str | None = None


class UserRegistryEntry(RegistryEntry):
    """One row of ``config/auth/list`` — every HA user account, the raw material
    for Layer 3 actor classification (docs/05-provenance.md §4, hia.provenance.actors):
    a context's ``user_id`` is always one of these ids, and ``system_generated``/
    ``name`` feed the auto-suggestion heuristics there."""

    id: str
    username: str | None = None
    name: str
    is_owner: bool = False
    is_active: bool = True
    local_only: bool = False
    system_generated: bool = False
    group_ids: list[str] = []


class ConnectionState(StrEnum):
    """Lifecycle of the underlying websocket connection, exposed for callers that
    want to reflect it (e.g. a UI status dot) without reaching into client internals.
    """

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"
