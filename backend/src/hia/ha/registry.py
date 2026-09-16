"""Syncs the entity/device/area/floor/label registries.

Areas and floors are the skeleton the digital twin's room topology is built from
(docs/02-architecture.md); entities and devices are what gets placed inside it.

Floor and label registries are a newer part of Home Assistant's API — an older Core
version may not support the corresponding commands. That is treated as "this
household doesn't have floors/labels configured" (empty list, logged once) rather
than a fatal error; every other registry is required and failures there propagate.
"""

from __future__ import annotations

from dataclasses import dataclass

from hia.ha.client import CommandError, HomeAssistantClient
from hia.ha.models import (
    AreaRegistryEntry,
    DeviceRegistryEntry,
    EntityRegistryEntry,
    FloorRegistryEntry,
    LabelRegistryEntry,
    UserRegistryEntry,
)
from hia.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Registries:
    entities: list[EntityRegistryEntry]
    devices: list[DeviceRegistryEntry]
    areas: list[AreaRegistryEntry]
    floors: list[FloorRegistryEntry]
    labels: list[LabelRegistryEntry]
    users: list[UserRegistryEntry]


async def fetch_entities(client: HomeAssistantClient) -> list[EntityRegistryEntry]:
    rows = await client.call({"type": "config/entity_registry/list"})
    return [EntityRegistryEntry.model_validate(row) for row in rows]


async def fetch_devices(client: HomeAssistantClient) -> list[DeviceRegistryEntry]:
    rows = await client.call({"type": "config/device_registry/list"})
    return [DeviceRegistryEntry.model_validate(row) for row in rows]


async def fetch_areas(client: HomeAssistantClient) -> list[AreaRegistryEntry]:
    rows = await client.call({"type": "config/area_registry/list"})
    return [AreaRegistryEntry.model_validate(row) for row in rows]


async def fetch_floors(client: HomeAssistantClient) -> list[FloorRegistryEntry]:
    try:
        rows = await client.call({"type": "config/floor_registry/list"})
    except CommandError:
        logger.warning("floor_registry_unavailable")
        return []
    return [FloorRegistryEntry.model_validate(row) for row in rows]


async def fetch_labels(client: HomeAssistantClient) -> list[LabelRegistryEntry]:
    try:
        rows = await client.call({"type": "config/label_registry/list"})
    except CommandError:
        logger.warning("label_registry_unavailable")
        return []
    return [LabelRegistryEntry.model_validate(row) for row in rows]


async def fetch_users(client: HomeAssistantClient) -> list[UserRegistryEntry]:
    """``config/auth/list`` — requires an admin token, same as the automation
    config REST endpoint (hia.ha.automations); confirmed by reading
    homeassistant/components/config/auth.py directly, not assumed. The raw
    material for Layer 3 actor classification (docs/05-provenance.md §4).
    Degrades to an empty list, like floors/labels, rather than failing the whole
    sync — not every configured token will be admin-privileged, and losing actor
    tagging is a reasonable degradation, not a reason to break basic operation."""
    try:
        rows = await client.call({"type": "config/auth/list"})
    except CommandError:
        logger.warning("user_registry_unavailable")
        return []
    return [UserRegistryEntry.model_validate(row) for row in rows]


async def fetch_all(client: HomeAssistantClient) -> Registries:
    return Registries(
        entities=await fetch_entities(client),
        devices=await fetch_devices(client),
        areas=await fetch_areas(client),
        floors=await fetch_floors(client),
        labels=await fetch_labels(client),
        users=await fetch_users(client),
    )
