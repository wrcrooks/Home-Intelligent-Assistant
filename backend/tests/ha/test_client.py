"""Exercises HomeAssistantClient against the fake server in tests/conftest.py:
auth (success and rejection), event delivery, reconnect + resubscribe with the
monotonic seq/gap contract, and dropping under backpressure rather than blocking.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from hia.ha.client import AuthenticationError, CommandError, HomeAssistantClient
from tests.conftest import VALID_TOKEN, FakeHomeAssistant


def _state_changed_data(entity_id: str, new_state: str = "on") -> dict:
    return {
        "entity_id": entity_id,
        "old_state": None,
        "new_state": {
            "entity_id": entity_id,
            "state": new_state,
            "attributes": {},
            "last_changed": "2026-09-15T12:00:00+00:00",
            "last_updated": "2026-09-15T12:00:00+00:00",
            "context": {"id": "ctx-1"},
        },
    }


async def test_authenticates_and_delivers_events(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        events_iter = client.events(["state_changed"])
        try:

            async def push_soon() -> None:
                await server.wait_for_subscriptions(1)
                await server.push_event("state_changed", _state_changed_data("light.kitchen"))

            pusher = asyncio.create_task(push_soon())
            watched = await anext(events_iter)
            await pusher

            assert watched.seq == 1
            assert watched.resumed_after_gap is False
            assert watched.event.event_type == "state_changed"
            state_changed = watched.event.as_state_changed()
            assert state_changed is not None
            assert state_changed.entity_id == "light.kitchen"
            assert state_changed.new_state is not None
            assert state_changed.new_state.state == "on"
            assert server.subscriptions == ["state_changed"]
        finally:
            await events_iter.aclose()


async def test_invalid_token_raises_authentication_error(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    _server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, "wrong-token", session=session)
        events_iter = client.events(["state_changed"])
        with pytest.raises(AuthenticationError):
            await anext(events_iter)
        await events_iter.aclose()


async def test_reconnects_and_resubscribes_with_monotonic_seq(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(
            base_url,
            VALID_TOKEN,
            session=session,
            initial_delay=0.01,
            max_delay=0.02,
        )
        events_iter = client.events(["state_changed"])
        try:

            async def first_round() -> None:
                await server.wait_for_subscriptions(1)
                await server.push_event("state_changed", _state_changed_data("light.kitchen"))

            asyncio.create_task(first_round())
            first = await anext(events_iter)
            assert first.seq == 1
            assert first.resumed_after_gap is False

            # Simulate the Core restart the P0 exit criterion cares about.
            await server.disconnect_all()

            async def second_round() -> None:
                await server.wait_for_subscriptions(2)  # initial connect + reconnect
                await server.push_event("state_changed", _state_changed_data("light.kitchen"))

            asyncio.create_task(second_round())
            second = await anext(events_iter)

            assert second.seq == 2  # monotonic across the reconnect
            assert second.resumed_after_gap is True
            assert server.subscriptions == ["state_changed", "state_changed"]
        finally:
            await events_iter.aclose()


async def test_events_delivered_while_a_later_subscription_is_still_pending(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    """Reproduces the real bug: subscribing to multiple event types, where Home
    Assistant sends a real event for an *already-acknowledged* earlier subscription
    before the result for a *later* one — reliably seen right after a Core restart,
    when many entities fire near-simultaneous events. An earlier version of
    `_subscribe_all` read exactly one message per pending subscription and assumed
    it was always that subscription's own result, misread the interleaved event as
    a failed subscription, and raised — only caught by testing a real reconnect
    against a live instance. See hia.ha.client._read_into_queue's docstring."""
    server, base_url = fake_ha
    server.interleave_event_before_subscription_result(
        before_subscribing_to="call_service",  # the last of the four below
        push_event_type="state_changed",
        push_data=_state_changed_data("light.kitchen"),
    )

    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        events_iter = client.events(
            ["state_changed", "automation_triggered", "script_started", "call_service"]
        )
        try:
            watched = await anext(events_iter)
            state_changed = watched.event.as_state_changed()
            assert state_changed is not None
            assert state_changed.entity_id == "light.kitchen"

            await server.wait_for_subscriptions(4)
            assert server.subscriptions == [
                "state_changed",
                "automation_triggered",
                "script_started",
                "call_service",
            ]
        finally:
            await events_iter.aclose()


async def test_drops_events_under_backpressure_instead_of_blocking(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    """Wraps the *consumer* (`anext(events_iter)`) in a Task and awaits the push
    side directly -- the exact inverse of every other test in this file, which
    task-wraps the side action (`push_soon`) and awaits `anext` directly in the
    test's own coroutine. That inversion is what hung this test indefinitely on
    GitHub Actions' Linux runners (never reproduced locally, even with the same
    pytest-timeout plugin installed): wrapping a live async-generator `anext()`
    call in a Task, then relying on cancelling *that* Task from the outside
    (originally via `asyncio.wait_for`) to unwind it, raced against the
    generator's own `finally:` cleanup (cancelling its reader task) in a way
    that occasionally never resolved. Rewritten to match the proven pattern used
    everywhere else in this file -- direct `await anext(events_iter)` in the
    test's own coroutine, `push_soon` as the background task -- removes the
    inverted wrap-and-cancel structure instead of trying to outrace it.
    pytest-timeout (pyproject.toml) remains as the suite-wide safety net for
    anything like this that recurs."""
    server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(
            base_url, VALID_TOKEN, session=session, queue_max_size=2
        )
        events_iter = client.events(["state_changed"])
        try:

            async def push_soon() -> None:
                await server.wait_for_subscriptions(1)
                for i in range(10):
                    await server.push_event(
                        "state_changed", _state_changed_data(f"sensor.s{i}")
                    )

            pusher = asyncio.create_task(push_soon())
            first = await anext(events_iter)
            await pusher

            assert first.seq == 1
            assert client.dropped_event_count > 0
        finally:
            await events_iter.aclose()


async def test_call_raises_on_unsupported_command(
    fake_ha: tuple[FakeHomeAssistant, str],
) -> None:
    _server, base_url = fake_ha
    async with aiohttp.ClientSession() as session:
        client = HomeAssistantClient(base_url, VALID_TOKEN, session=session)
        with pytest.raises(CommandError):
            await client.call({"type": "config/floor_registry/list"})
