"""Registry sync: the required registries parse, and the two newer/optional ones
(floor, label) degrade to an empty list rather than failing the whole sync when a
Home Assistant instance doesn't support them.
"""

from __future__ import annotations

import aiohttp

from hia.ha.client import HomeAssistantClient
from hia.ha.registry import fetch_all, fetch_users
from tests.conftest import VALID_TOKEN, FakeHomeAssistant


async def test_fetch_all_parses_known_registries_and_tolerates_missing_ones(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    server.set_registry_response(
        "config/entity_registry/list",
        [{"entity_id": "light.kitchen", "platform": "demo", "area_id": "kitchen"}],
    )
    server.set_registry_response(
        "config/device_registry/list",
        [{"id": "dev1", "name": "Kitchen Light", "manufacturer": "Acme"}],
    )
    server.set_registry_response(
        "config/area_registry/list",
        [{"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"}],
    )
    server.set_registry_response(
        "config/auth/list",
        [{"id": "user1", "name": "Will", "is_owner": True, "system_generated": False}],
    )
    # Deliberately not registering config/floor_registry/list or
    # config/label_registry/list — the fake server will answer both with
    # success: false, exactly like an older Home Assistant Core that predates them.

    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        registries = await fetch_all(client)

    assert [e.entity_id for e in registries.entities] == ["light.kitchen"]
    assert [d.id for d in registries.devices] == ["dev1"]
    assert [a.area_id for a in registries.areas] == ["kitchen"]
    assert registries.floors == []
    assert registries.labels == []
    assert [u.id for u in registries.users] == ["user1"]


async def test_fetch_users_degrades_to_empty_list_on_a_non_admin_token(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    """config/auth/list requires an admin token, like the automation config REST
    endpoint (hia.ha.automations) -- a non-admin token must not break the whole
    registry sync, just leave actor tagging unavailable."""
    _server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        assert await fetch_users(client) == []
