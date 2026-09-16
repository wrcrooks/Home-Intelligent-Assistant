// Talks to hia serve (backend/src/hia/api/). Every URL here is built relative to
// document.baseURI, never an absolute "/api/..." path — under Home Assistant's
// ingress this app is served from a per-install, runtime-assigned prefix (see
// vite.config.ts's `base: "./"` comment), and only paths resolved relative to the
// document's own URL follow that prefix automatically. An absolute path would work
// in local dev and silently break under ingress.

import type {
  ActorClass,
  ActorRow,
  HourlyActivity,
  LatestState,
  QualityReport,
  StateChangedMessage,
} from "./types";

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

export async function fetchHourlyActivity(): Promise<HourlyActivity[]> {
  const res = await fetch(new URL("state-changes/hourly", API_BASE));
  if (!res.ok) throw new Error(`GET /api/state-changes/hourly failed: ${res.status}`);
  return (await res.json()) as HourlyActivity[];
}

/**
 * Layer 3's setup task (docs/05-provenance.md §4): fetches HA's live user
 * registry merged with observed event counts, a heuristic suggestion, and any
 * classification already confirmed — see hia.api.actors.build_actors_payload.
 * Slower than the other GETs (it makes its own live round trip to Home
 * Assistant, `config/auth/list`), so callers should fetch it once per tab
 * visit, not poll it.
 */
export async function fetchActors(): Promise<ActorRow[]> {
  const res = await fetch(new URL("provenance/actors", API_BASE));
  if (!res.ok) throw new Error(`GET /api/provenance/actors failed: ${res.status}`);
  return (await res.json()) as ActorRow[];
}

/** An owner's actual confirmation — the only thing allowed to set an actor's
 * classification (hia.provenance.actors' module docstring: a suggestion is
 * never itself a classification). */
export async function confirmActor(userId: string, actorClass: ActorClass): Promise<void> {
  const res = await fetch(new URL(`provenance/actors/${encodeURIComponent(userId)}`, API_BASE), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor_class: actorClass }),
  });
  if (!res.ok) throw new Error(`POST /api/provenance/actors/${userId} failed: ${res.status}`);
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
