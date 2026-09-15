"""A fake Home Assistant websocket server, just complete enough to exercise
:class:`hia.ha.client.HomeAssistantClient` without a real HA instance: the auth
handshake, ``subscribe_events``, event delivery, registry commands, and — via
``disconnect_all`` — forced disconnects to test reconnect and resubscription.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

if sys.platform == "win32":
    # hia.api's tests run two event loops concurrently on separate threads
    # (pytest-asyncio's for this fixture's fake server, and starlette
    # TestClient's own portal thread for the WebSocket-under-test). The default
    # ProactorEventLoop's IOCP polling is unstable across threads on Windows in
    # exactly that configuration (a reproducible `Windows fatal exception: access
    # violation` inside asyncio's windows_events._poll, not a bug in this
    # project's own code). SelectorEventLoop doesn't have that failure mode and
    # this project spawns no subprocesses in tests, so it loses nothing here.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

VALID_TOKEN = "test-token"


class FakeHomeAssistant:
    def __init__(self) -> None:
        self.app = web.Application()
        self.app.router.add_get("/api/websocket", self._handle_ws)
        self.sockets: list[web.WebSocketResponse] = []
        self.subscriptions: list[str] = []
        self.rejected_connections = 0
        self._registry_responses: dict[str, list[dict[str, Any]]] = {}
        self._subscription_event = asyncio.Event()
        self._interleave: tuple[str, str, dict[str, Any]] | None = None

    def set_registry_response(self, command: str, rows: list[dict[str, Any]]) -> None:
        self._registry_responses[command] = rows

    def interleave_event_before_subscription_result(
        self, *, before_subscribing_to: str, push_event_type: str, push_data: dict[str, Any]
    ) -> None:
        """Reproduces the real-world race that broke ``_subscribe_all``: when the
        subscribe_events request for ``before_subscribing_to`` arrives, send a real
        event *first*, then that subscription's own result — exactly what Home
        Assistant can do when a burst of events (typical right after a Core
        restart) lands while a later subscription in the same batch is still
        pending."""
        self._interleave = (before_subscribing_to, push_event_type, push_data)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        await ws.send_json({"type": "auth_required", "ha_version": "2026.9.0"})
        auth_msg = await ws.receive_json()
        if auth_msg.get("access_token") != VALID_TOKEN:
            self.rejected_connections += 1
            await ws.send_json({"type": "auth_invalid", "message": "invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.9.0"})
        self.sockets.append(ws)

        async for raw in ws:
            if raw.type != WSMsgType.TEXT:
                continue
            message = raw.json()
            msg_type = message.get("type")
            msg_id = message.get("id")

            if msg_type == "subscribe_events":
                event_type = message["event_type"]
                self.subscriptions.append(event_type)
                self._subscription_event.set()
                if self._interleave is not None and self._interleave[0] == event_type:
                    _, push_type, push_data = self._interleave
                    self._interleave = None
                    await self.push_event(push_type, push_data)
                await ws.send_json(
                    {"id": msg_id, "type": "result", "success": True, "result": None}
                )
            elif msg_type in self._registry_responses:
                await ws.send_json(
                    {
                        "id": msg_id,
                        "type": "result",
                        "success": True,
                        "result": self._registry_responses[msg_type],
                    }
                )
            elif msg_type is not None:
                await ws.send_json(
                    {
                        "id": msg_id,
                        "type": "result",
                        "success": False,
                        "error": {"code": "unknown_command", "message": str(msg_type)},
                    }
                )

        if ws in self.sockets:
            self.sockets.remove(ws)
        return ws

    async def push_event(
        self, event_type: str, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> None:
        payload = {
            "id": 1,
            "type": "event",
            "event": {
                "event_type": event_type,
                "data": data,
                "origin": "LOCAL",
                "time_fired": "2026-09-15T12:00:00+00:00",
                "context": context or {"id": "ctx-1"},
            },
        }
        for ws in list(self.sockets):
            if not ws.closed:
                await ws.send_json(payload)

    async def disconnect_all(self) -> None:
        """Force-close every open connection, to exercise the client's reconnect
        and resubscribe logic."""
        for ws in list(self.sockets):
            await ws.close()

    async def wait_for_subscriptions(self, count: int, *, timeout_seconds: float = 2.0) -> None:
        """Wait until at least ``count`` subscribe_events commands have been seen
        (across all connections, so this also observes resubscription after a
        forced reconnect)."""
        async with asyncio.timeout(timeout_seconds):
            while len(self.subscriptions) < count:
                self._subscription_event.clear()
                if len(self.subscriptions) >= count:
                    break
                await self._subscription_event.wait()


@pytest.fixture
async def fake_ha() -> AsyncIterator[tuple[FakeHomeAssistant, str]]:
    server = FakeHomeAssistant()
    test_server = TestServer(server.app)
    await test_server.start_server()
    base_url = f"http://{test_server.host}:{test_server.port}"
    try:
        yield server, base_url
    finally:
        await test_server.close()
