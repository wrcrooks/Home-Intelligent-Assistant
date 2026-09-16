"""Fetches automation configurations from Home Assistant and extracts the entities
each automation is capable of acting on.

This is the raw material for Layer 2 correlation (docs/05-provenance.md §4): the
critical fact that closes the sun/time-trigger hole is that ``automation_triggered``
still fires on the event bus even when the trigger itself set no context. Knowing
*which entities an automation could touch* is what lets a firing be matched to the
state change it caused, on timing alone, when context gives no other way in.

Automation configs are not available over the websocket API at all — the only way
to read one is Home Assistant's REST-only ``config`` component,
``GET /api/config/automation/config/{config_id}``. Confirmed by reading
``homeassistant/components/config/automation.py`` and its ``BaseEditConfigView``
base class directly (undocumented in the public REST API reference at
https://developers.home-assistant.io/docs/api/rest/, which doesn't mention this
endpoint at all). ``config_id`` is the automation's own ``id`` field from
``automations.yaml``, not its ``entity_id`` — every automation HA loads has one,
auto-assigned if the user didn't set one (the platform schema requires
``CONF_ID: str``), and it's what the entity registry's ``unique_id`` is set to for
automation entities. This endpoint requires an admin-privileged token
(``@require_admin`` in HA's source) — a non-admin long-lived access token gets a
403 here even though the same token works fine for the websocket API used
everywhere else in this project. Not yet verified against a real admin vs.
non-admin token; flagged in docs/HANDOFF.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hia.ha.client import HomeAssistantClient
from hia.ha.models import EntityRegistryEntry
from hia.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AutomationTargets:
    entity_id: str
    config_id: str
    targets: frozenset[str]


async def fetch_targets(
    client: HomeAssistantClient, entities: list[EntityRegistryEntry]
) -> list[AutomationTargets]:
    """One :class:`AutomationTargets` per automation entity whose config could be
    fetched and parsed. Automations with no ``unique_id`` (shouldn't happen — every
    loaded automation has an id — but registry data from a real house has already
    surprised this project once) or whose config 404s are skipped and logged, not
    raised: one unreadable automation must never block classifying every other
    event.

    Every other entity in the registry (the overwhelming majority of it, on a
    real house — sensors, sun/weather entities, anything that isn't an
    automation) is silently skipped, not logged: reproduced live against a
    real 482-entity registry, an earlier version of this loop logged
    ``automation_missing_unique_id`` for every single non-automation entity
    (the ``or`` combined "not an automation" with "an automation missing its
    id" into one branch and one misleading message), which is just normal
    filtering, not a warning-worthy condition."""
    results: list[AutomationTargets] = []
    for entity in entities:
        if not entity.entity_id.startswith("automation."):
            continue
        if not entity.unique_id:
            logger.warning("automation_missing_unique_id", entity_id=entity.entity_id)
            continue
        config = await client.get(f"/api/config/automation/config/{entity.unique_id}")
        if config is None:
            logger.warning(
                "automation_config_not_found",
                entity_id=entity.entity_id,
                config_id=entity.unique_id,
            )
            continue
        actions = config.get("actions", config.get("action"))
        results.append(
            AutomationTargets(
                entity_id=entity.entity_id,
                config_id=entity.unique_id,
                targets=frozenset(extract_action_targets(actions)),
            )
        )
    return results


_NON_TARGET_KEYS = frozenset({"condition", "conditions", "if"})
"""Keys whose value is entity_ids this automation *reads* to decide whether to
run, not entities it *acts on* — a `choose` block's own `conditions` list, or an
`if` action's `if:` block, are the common real cases (docs/05-provenance.md §4
wants "the entities it is capable of touching", not everything it merely
inspects). Skipped entirely rather than walked, so an entity used only as a
gating condition never shows up as a false positive target."""


def extract_action_targets(node: Any) -> set[str]:
    """Recursively collect every ``entity_id`` reachable from an automation's
    ``action``/``actions`` block, however deeply it's nested inside
    ``choose``/``if``/``parallel``/``repeat`` or any future construct HA adds.
    Walking generically by key name (``entity_id``, wherever it appears — a bare
    field on an action or nested under ``target:``) rather than enumerating action
    types means a new action type is silently included, not silently missed. HA's
    action-targeting syntax has moved around over the years (``entity_id:``
    directly on the old-style action, ``target: {entity_id: ...}`` on the new one,
    either a bare string or a list) — this collects all of them without needing to
    know which era wrote the config. ``condition``/``conditions`` sub-blocks
    (``choose``'s own gating conditions, an ``if`` action's ``if:``) are skipped —
    see :data:`_NON_TARGET_KEYS`."""
    found: set[str] = set()
    _walk(node, found)
    return found


def _walk(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        value = node.get("entity_id")
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, list):
            found.update(v for v in value if isinstance(v, str))
        for key, child in node.items():
            if key in _NON_TARGET_KEYS:
                continue
            _walk(child, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)
