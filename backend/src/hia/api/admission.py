"""Builds the payload behind ``GET /api/provenance/admission`` — admission
control (docs/05-provenance.md §6), computed fresh on every request against a
live automation-target fetch and the store's accumulated history. Mirrors
``hia.api.actors``'s shape: a thin orchestration layer between the live HA
client, the store, and the pure ``hia.provenance`` logic.
"""

from __future__ import annotations

from hia.ha.automations import fetch_targets
from hia.ha.client import HomeAssistantClient
from hia.ha.registry import fetch_entities
from hia.ingest.store import EventStore
from hia.provenance.admission import EntityAdmission, compute_admission


async def build_admission_payload(
    client: HomeAssistantClient, store: EventStore
) -> list[EntityAdmission]:
    entities = await fetch_entities(client)
    automation_targets = await fetch_targets(client, entities)
    targets = {a.entity_id: a.targets for a in automation_targets}
    return compute_admission(store, targets)
