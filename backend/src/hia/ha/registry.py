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


async def fetch_all(client: HomeAssistantClient) -> Registries:
    return Registries(
        entities=await fetch_entities(client),
        devices=await fetch_devices(client),
        areas=await fetch_areas(client),
        floors=await fetch_floors(client),
        labels=await fetch_labels(client),
    )
