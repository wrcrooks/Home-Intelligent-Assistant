"""hia.api.admission: GET /api/provenance/admission's logic.

Tested by calling build_admission_payload directly against the fake HA
server, not through a real TestClient request -- see tests/api/test_actors.py's
module docstring for why: a route handler that makes its own outbound async
HA call hangs TestClient's cross-thread portal bridge in this environment,
reproduced there, not assumed to apply here too."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiohttp

from hia.api.admission import build_admission_payload
from hia.ha.client import HomeAssistantClient
from hia.ha.models import Context, HAEvent
from hia.ingest.store import EventStore
from tests.conftest import VALID_TOKEN, FakeHomeAssistant


def _write_automation_fire(store: EventStore, entity_id: str, context_id: str, ts: datetime) -> None:
    store.write_event(
        HAEvent(
            event_type="automation_triggered",
            data={"entity_id": entity_id},
            origin="LOCAL",
            time_fired=ts,
            context=Context(id=context_id),
        ),
        source="live",
    )


def _write_state_change(
    store: EventStore, entity_id: str, ts: datetime, *, context_id: str, context_parent_id: str
) -> None:
    store.write_state_change(
        entity_id=entity_id,
        state="on",
        attributes=None,
        old_state=None,
        last_changed=ts,
        last_updated=ts,
        context_id=context_id,
        context_parent_id=context_parent_id,
        context_user_id=None,
        source="live",
    )


async def test_build_admission_payload_fetches_real_automation_targets(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    server.set_registry_response(
        "config/entity_registry/list",
        [{"entity_id": "automation.porch", "unique_id": "porch-id", "platform": "automation"}],
    )
    server.set_automation_config(
        "porch-id",
        {"actions": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]},
    )

    with EventStore(":memory:") as store:
        now = datetime.now(UTC)
        for i in range(9):
            ts = now - timedelta(hours=i)
            _write_automation_fire(store, "automation.porch", f"ctx-fire-{i}", ts)
            _write_state_change(
                store, "light.porch", ts, context_id=f"ctx-sc-{i}", context_parent_id=f"ctx-fire-{i}"
            )

        async with aiohttp.ClientSession() as session:
            client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
            admissions = await build_admission_payload(client, store)

    [porch] = [a for a in admissions if a.entity_id == "light.porch"]
    assert porch.automation_share == 1.0
    assert porch.treatment == "excluded"
    assert porch.responsible_automations == ("automation.porch",)
