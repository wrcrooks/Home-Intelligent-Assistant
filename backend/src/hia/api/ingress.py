"""Restricting inbound connections to Home Assistant's Supervisor proxy when
running as an add-on (docs/02-architecture.md `api/` + `ui/`, docs/HANDOFF.md
platform facts).

A pure ASGI middleware, deliberately not Starlette's ``BaseHTTPMiddleware`` —
``BaseHTTPMiddleware`` only ever sees HTTP-scope requests, and silently does not
intercept ``websocket`` scope connections at all. Since the live-event relay
(``hia.api.live``) is a WebSocket endpoint and is exactly the kind of thing this
restriction exists to protect, a middleware that only guards HTTP would leave it
open. Implementing the raw ASGI interface handles every scope type uniformly by
construction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SUPERVISOR_PROXY_IP = "172.30.32.2"
"""The fixed source IP Home Assistant's Supervisor uses when proxying ingress
traffic to an add-on (docs/HANDOFF.md platform facts)."""


class RestrictToSupervisorMiddleware:
    """When ``enabled``, only allows HTTP and WebSocket connections whose client IP
    is the Supervisor's ingress proxy; everything else gets a 403 (HTTP) or an
    immediate close (WebSocket). Disabled by default for local development —
    ``hia.cli.serve`` enables it automatically when ``Settings.is_addon``."""

    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = client[0] if client else None
        if host != SUPERVISOR_PROXY_IP:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
            else:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"detail":"forbidden"}'})
            return

        await self.app(scope, receive, send)
