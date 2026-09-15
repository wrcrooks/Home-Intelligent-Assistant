import type { LatestState, StateChangedMessage } from "./types";

/**
 * Merges one live state_changed message into a snapshot of latest-known entity
 * states, keyed by entity_id — replace if already present, insert (sorted) if new.
 *
 * Pulled out as its own pure function specifically so this logic is unit-testable
 * without a real WebSocket or a rendered component (see entityStore.test.ts).
 *
 * The relay's broadcast message (hia.api.live.state_changed_message) only carries
 * `last_updated`, not `last_changed` — HA distinguishes the two (attributes-only
 * changes bump `last_updated` without bumping `last_changed`), and fabricating a
 * value here would silently claim something the relay never actually said.
 * `last_changed` is left `null` on a live-applied row rather than guessed.
 */
export function applyStateChanged(
  entities: LatestState[],
  message: StateChangedMessage,
): LatestState[] {
  const updated: LatestState = {
    entity_id: message.entity_id,
    state: message.state,
    attributes: message.attributes,
    last_changed: null,
    last_updated: message.last_updated,
  };

  const index = entities.findIndex((e) => e.entity_id === message.entity_id);
  if (index === -1) {
    return [...entities, updated].sort((a, b) => a.entity_id.localeCompare(b.entity_id));
  }
  const next = entities.slice();
  next[index] = updated;
  return next;
}
