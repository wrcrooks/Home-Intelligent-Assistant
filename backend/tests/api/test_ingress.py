"""RestrictToSupervisorMiddleware, tested directly against synthetic ASGI scopes.

TestClient's httpx-based transport has no way to spoof the ASGI scope's client IP,
and getting this exactly right matters — see the module docstring in
hia.api.ingress on why it can't be a Starlette BaseHTTPMiddleware.
"""

from __future__ import annotations

from typing import Any

from hia.api.ingress import SUPERVISOR_PROXY_IP, RestrictToSupervisorMiddleware


class _RecordingApp:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True


def _scope(scope_type: str, client_ip: str | None) -> dict[str, Any]:
    return {"type": scope_type, "client": (client_ip, 12345) if client_ip else None}


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request"}


def _recording_send() -> tuple[list[dict[str, Any]], Any]:
    """``send`` is async per the ASGI spec (``Awaitable[None]``) — a plain
    ``list.append`` fails at the first ``await send(...)``, not silently."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    return sent, send


async def test_disabled_allows_any_client() -> None:
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=False)
    _sent, send = _recording_send()
    await middleware(_scope("http", "1.2.3.4"), _noop_receive, send)
    assert inner.called is True


async def test_enabled_allows_the_supervisor_ip() -> None:
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=True)
    _sent, send = _recording_send()
    await middleware(_scope("http", SUPERVISOR_PROXY_IP), _noop_receive, send)
    assert inner.called is True


async def test_enabled_rejects_other_clients_over_http() -> None:
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=True)
    sent, send = _recording_send()
    await middleware(_scope("http", "1.2.3.4"), _noop_receive, send)
    assert inner.called is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 403


async def test_enabled_rejects_other_clients_over_websocket() -> None:
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=True)
    sent, send = _recording_send()
    await middleware(_scope("websocket", "1.2.3.4"), _noop_receive, send)
    assert inner.called is False
    assert sent == [{"type": "websocket.close", "code": 4403}]


async def test_enabled_rejects_a_missing_client_ip() -> None:
    """No `client` in the scope at all (some non-HTTP transports) must fail closed,
    not be treated as "no restriction to apply"."""
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=True)
    _sent, send = _recording_send()
    await middleware(_scope("http", None), _noop_receive, send)
    assert inner.called is False


async def test_enabled_ignores_the_lifespan_scope() -> None:
    """The lifespan scope isn't a "connection" with a client IP at all — must pass
    through untouched or app startup/shutdown would never run."""
    inner = _RecordingApp()
    middleware = RestrictToSupervisorMiddleware(inner, enabled=True)
    _sent, send = _recording_send()
    await middleware({"type": "lifespan"}, _noop_receive, send)
    assert inner.called is True
