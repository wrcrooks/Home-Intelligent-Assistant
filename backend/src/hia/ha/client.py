"""Websocket client for Home Assistant's Core/Supervisor API.

Handles the auth handshake, event subscription, automatic reconnect with
resubscription, and a monotonic event sequence number so consumers can detect gaps
caused by a reconnect (docs/02-architecture.md, `ha/` client). Protocol details
(auth_required/auth/auth_ok, subscribe_events, message ids) verified against
https://developers.home-assistant.io/docs/api/websocket/.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import random
from collections.abc import AsyncIterator, Iterable
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from hia.ha.models import HAEvent, WatchedEvent
from hia.logging import get_logger

logger = get_logger(__name__)

# Sentinel pushed onto the internal queue when the reader task ends (connection
# closed or errored), so the consumer loop wakes up and notices without needing a
# second wait primitive.
_CLOSED: Final[object] = object()

_TERMINAL_WS_TYPES: Final = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
)


class AuthenticationError(RuntimeError):
    """Raised when Home Assistant rejects the access token. Not retried — a bad
    token will not become good token on the next reconnect attempt."""


class CommandError(RuntimeError):
    """Raised when a websocket command gets back ``success: false``."""


def websocket_url(base_url: str) -> str:
    """Turn a configured base URL (``http://``, ``https://``, ``ws://`` or ``wss://``,
    with or without a path) into the websocket endpoint URL."""
    parts = urlsplit(base_url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    if scheme not in ("ws", "wss"):
        raise ValueError(f"Unsupported scheme in Home Assistant URL: {base_url!r}")
    return urlunsplit((scheme, parts.netloc, "/api/websocket", "", ""))


class HomeAssistantClient:
    """Long-lived subscriber to Home Assistant's websocket API.

    ``events()`` runs until the caller stops iterating (or cancels the task): on any
    transport failure it reconnects with exponential backoff and jitter,
    re-authenticates, and resubscribes to every event type originally requested. Only
    :class:`AuthenticationError` propagates — a rejected token will not fix itself on
    retry, everything else is treated as recoverable.

    A background reader task keeps draining the socket independently of how fast the
    caller consumes ``events()``, so a slow consumer can never delay this client's
    responsiveness to the server (heartbeats, close frames). If the caller falls far
    enough behind that the internal queue fills, the newest event is dropped and
    counted (:attr:`dropped_event_count`) rather than the reader blocking — for a
    live event stream, a bounded gap is preferable to unbounded memory growth or a
    stalled connection.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session: aiohttp.ClientSession,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        queue_max_size: int = 2000,
    ) -> None:
        self._url = websocket_url(base_url)
        self._token = token
        self._session = session
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._backoff_factor = backoff_factor
        self._queue_max_size = queue_max_size
        self._dropped_events = 0
        self._seq = itertools.count(1)

    @property
    def dropped_event_count(self) -> int:
        return self._dropped_events

    async def events(self, event_types: Iterable[str]) -> AsyncIterator[WatchedEvent]:
        """Yield events forever, reconnecting transparently on disconnect."""
        event_types = list(event_types)
        queue: asyncio.Queue[HAEvent | object] = asyncio.Queue(maxsize=self._queue_max_size)
        delay = self._initial_delay
        first_connection = True

        while True:
            try:
                async with self._session.ws_connect(self._url, heartbeat=30) as ws:
                    await self._authenticate(ws)
                    pending_subscriptions = await self._subscribe_all(ws, event_types)
                    logger.info(
                        "ha_connected",
                        url=self._url,
                        resumed=not first_connection,
                        event_types=event_types,
                    )
                    delay = self._initial_delay  # a clean connect resets backoff
                    resumed_after_gap = not first_connection
                    first_connection = False

                    reader_task = asyncio.create_task(
                        self._read_into_queue(ws, queue, pending_subscriptions)
                    )
                    try:
                        async for item in self._drain(queue):
                            yield WatchedEvent(
                                seq=next(self._seq),
                                resumed_after_gap=resumed_after_gap,
                                event=item,
                            )
                            resumed_after_gap = False
                        await reader_task  # surface a reader exception, if any
                    finally:
                        if not reader_task.done():
                            reader_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await reader_task

            except AuthenticationError:
                raise
            except (aiohttp.ClientError, ConnectionError, TimeoutError) as exc:
                logger.warning("ha_disconnected", error=str(exc), retry_in=round(delay, 1))

            jittered = delay * (0.8 + 0.4 * random.random())
            await asyncio.sleep(jittered)
            delay = min(delay * self._backoff_factor, self._max_delay)

    @staticmethod
    async def _drain(queue: asyncio.Queue[HAEvent | object]) -> AsyncIterator[HAEvent]:
        while True:
            item = await queue.get()
            if item is _CLOSED:
                return
            assert isinstance(item, HAEvent)
            yield item

    async def _read_into_queue(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        queue: asyncio.Queue[HAEvent | object],
        pending_subscriptions: dict[int, str],
    ) -> None:
        """The one loop that reads every message off this connection — both the
        `result` acknowledgements for the subscribe_events commands sent in
        :meth:`_subscribe_all` and the actual events those subscriptions produce.

        It has to be one loop, not "wait for N results, then start reading events":
        Home Assistant does not guarantee a subscription's result arrives before
        events from an *earlier* subscription in the same batch. Right after a
        reconnect especially, many entities fire near-simultaneous events, and one
        can easily arrive while a later subscribe_events call is still waiting on
        its own result. A version of this method that read exactly one message per
        pending subscription and assumed it was that subscription's result
        misread such an event as a failed subscription and raised — caught only by
        testing an actual Core restart against a live instance, since a test
        double that never interleaves messages this way can't reproduce it.
        """
        try:
            async for raw in ws:
                if raw.type is aiohttp.WSMsgType.ERROR:
                    raise ConnectionError(f"Websocket error: {ws.exception()}")
                if raw.type in _TERMINAL_WS_TYPES:
                    break
                if raw.type is not aiohttp.WSMsgType.TEXT:
                    continue

                message = raw.json()
                msg_type = message.get("type")

                if msg_type == "result":
                    event_type = pending_subscriptions.pop(message.get("id"), None)
                    if event_type is not None and not message.get("success", False):
                        logger.error(
                            "subscribe_failed", event_type=event_type, result=message
                        )
                    continue

                if msg_type != "event":
                    continue
                event = HAEvent.model_validate(message["event"])
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._dropped_events += 1
                    logger.warning(
                        "event_queue_full_dropping_event",
                        dropped_total=self._dropped_events,
                    )
        finally:
            await queue.put(_CLOSED)

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        first = await ws.receive_json()
        if first.get("type") != "auth_required":
            raise AuthenticationError(f"Expected auth_required, got: {first}")

        await ws.send_json({"type": "auth", "access_token": self._token})
        reply = await ws.receive_json()
        if reply.get("type") == "auth_invalid":
            raise AuthenticationError(
                reply.get("message", "Home Assistant rejected the access token")
            )
        if reply.get("type") != "auth_ok":
            raise AuthenticationError(f"Unexpected auth response: {reply}")

    async def _subscribe_all(
        self, ws: aiohttp.ClientWebSocketResponse, event_types: list[str]
    ) -> dict[int, str]:
        """Sends a subscribe_events command per event type and returns a
        message-id -> event_type map of subscriptions awaiting confirmation.
        Deliberately does not wait for each result here — see
        :meth:`_read_into_queue`, the only place that can safely tell a result
        from an interleaved event."""
        pending: dict[int, str] = {}
        for msg_id, event_type in enumerate(event_types, start=1):
            await ws.send_json(
                {"id": msg_id, "type": "subscribe_events", "event_type": event_type}
            )
            pending[msg_id] = event_type
        return pending

    async def call(self, message: dict[str, Any]) -> Any:
        """One-shot request/response (registry lookups, service calls) over a fresh
        connection. Not used for the long-lived event stream in :meth:`events`."""
        async with self._session.ws_connect(self._url, heartbeat=30) as ws:
            await self._authenticate(ws)
            await ws.send_json({"id": 1, **message})
            result = await ws.receive_json()
            if not result.get("success", False):
                raise CommandError(f"Command {message.get('type')!r} failed: {result}")
            return result.get("result")
