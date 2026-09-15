// Talks to hia serve (backend/src/hia/api/). Every URL here is built relative to
// document.baseURI, never an absolute "/api/..." path — under Home Assistant's
// ingress this app is served from a per-install, runtime-assigned prefix (see
// vite.config.ts's `base: "./"` comment), and only paths resolved relative to the
// document's own URL follow that prefix automatically. An absolute path would work
// in local dev and silently break under ingress.

import type { LatestState, QualityReport, StateChangedMessage } from "./types";

const API_BASE = new URL("api/", document.baseURI);

export async function fetchEntities(): Promise<LatestState[]> {
  const res = await fetch(new URL("entities", API_BASE));
  if (!res.ok) throw new Error(`GET /api/entities failed: ${res.status}`);
  return (await res.json()) as LatestState[];
}

export async function fetchDataQuality(): Promise<QualityReport> {
  const res = await fetch(new URL("data-quality", API_BASE));
  if (!res.ok) throw new Error(`GET /api/data-quality failed: ${res.status}`);
  return (await res.json()) as QualityReport;
}

/**
 * Opens the live event relay and reconnects with backoff on drop — the same
 * "never give up, back off, try again" posture as the backend's own HA client
 * (docs/HANDOFF.md), just on the browser side of the same relay. Returns a
 * cleanup function that closes the socket and stops reconnecting.
 */
export function connectLiveEvents(
  onMessage: (message: StateChangedMessage) => void,
  onStatusChange: (connected: boolean) => void,
): () => void {
  let closedByCaller = false;
  let socket: WebSocket | null = null;
  let retryDelay = 1000;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  function connect(): void {
    const wsUrl = new URL("ws/events", API_BASE);
    wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(wsUrl);

    socket.addEventListener("open", () => {
      retryDelay = 1000;
      onStatusChange(true);
    });

    socket.addEventListener("message", (event: MessageEvent<string>) => {
      try {
        const message = JSON.parse(event.data) as StateChangedMessage;
        if (message.type === "state_changed") onMessage(message);
      } catch {
        // A malformed frame shouldn't take the whole live view down.
      }
    });

    socket.addEventListener("close", () => {
      onStatusChange(false);
      if (closedByCaller) return;
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30_000);
    });

    socket.addEventListener("error", () => {
      socket?.close();
    });
  }

  connect();

  return () => {
    closedByCaller = true;
    if (retryTimer) clearTimeout(retryTimer);
    socket?.close();
  };
}
