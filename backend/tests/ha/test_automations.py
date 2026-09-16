"""Exercises hia.ha.automations against the fake server's REST automation-config
route: fetching configs, mapping entity_id -> targets, and the target-extraction
walk over nested action structures."""

from __future__ import annotations

import aiohttp

from hia.ha.automations import extract_action_targets, fetch_targets
from hia.ha.client import HomeAssistantClient
from hia.ha.models import EntityRegistryEntry
from tests.conftest import VALID_TOKEN, FakeHomeAssistant


def _entity(entity_id: str, unique_id: str | None) -> EntityRegistryEntry:
    return EntityRegistryEntry(entity_id=entity_id, unique_id=unique_id)


async def test_fetches_and_extracts_targets_for_new_and_old_syntax(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    server.set_automation_config(
        "sunset-porch",
        {
            "alias": "Porch light at sunset",
            "triggers": [{"platform": "sun", "event": "sunset"}],
            "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        },
    )
    server.set_automation_config(
        "old-syntax",
        {
            "alias": "Old-style automation",
            "action": [{"service": "light.turn_on", "entity_id": ["light.hall", "light.foyer"]}],
        },
    )

    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        entities = [
            _entity("automation.sunset_porch", "sunset-porch"),
            _entity("automation.old_syntax", "old-syntax"),
        ]
        results = await fetch_targets(client, entities)

    by_entity = {r.entity_id: r for r in results}
    assert by_entity["automation.sunset_porch"].targets == frozenset({"light.porch"})
    assert by_entity["automation.old_syntax"].targets == frozenset(
        {"light.hall", "light.foyer"}
    )


async def test_skips_and_logs_missing_config_and_missing_unique_id(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    _server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        entities = [
            _entity("automation.deleted", "no-longer-exists"),
            _entity("automation.no_unique_id", None),
            _entity("light.not_an_automation", "irrelevant"),
        ]
        results = await fetch_targets(client, entities)

    assert results == []


def test_extract_action_targets_walks_nested_choose_and_parallel() -> None:
    actions = [
        {
            "choose": [
                {
                    "conditions": [{"condition": "state", "entity_id": "sensor.mode"}],
                    "sequence": [
                        {"service": "light.turn_on", "target": {"entity_id": "light.a"}}
                    ],
                }
            ],
            "default": [{"service": "light.turn_off", "entity_id": "light.b"}],
        },
        {
            "parallel": [
                {"service": "scene.turn_on", "target": {"entity_id": ["scene.movie"]}},
            ]
        },
    ]
    targets = extract_action_targets(actions)
    # sensor.mode is a *condition* entity, not an action target -- it must not
    # be conflated with something this automation can actually change.
    assert targets == {"light.a", "light.b", "scene.movie"}
    assert "sensor.mode" not in targets
