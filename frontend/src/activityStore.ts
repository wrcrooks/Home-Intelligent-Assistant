import type { HourlyActivity, StateChangedMessage } from "./types";

/**
 * Bumps the bucket a live state_changed message falls into, the same
 * "apply the live push directly, don't wait for the next poll" posture as
 * entityStore's applyStateChanged. Unlike the data-quality report (which
 * polls — see App.tsx's comment on why), state-change *counts* are exactly
 * what the live relay already carries, so there's no reason to wait.
 *
 * A message whose hour isn't currently in `data` at all (the rare case of a
 * live event landing right as the hour rolls over, just before the next
 * periodic re-fetch replaces the whole 24-bucket window) is silently dropped
 * rather than fabricating a new bucket out of sequence — the next poll
 * reconciles it for real.
 */
export function applyLiveStateChange(
  data: HourlyActivity[],
  message: StateChangedMessage,
): HourlyActivity[] {
  const changedAt = new Date(message.last_updated);
  if (Number.isNaN(changedAt.getTime())) return data;
  // UTC, not local: the backend buckets with DuckDB's date_trunc('hour', ...)
  // over a TIMESTAMPTZ column, which is UTC-aligned regardless of viewer
  // timezone — setMinutes (local) would misalign for any offset that isn't a
  // whole number of hours (e.g. India, UTC+5:30).
  changedAt.setUTCMinutes(0, 0, 0);

  const index = data.findIndex((b) => new Date(b.hour).getTime() === changedAt.getTime());
  if (index === -1) return data;

  const next = data.slice();
  next[index] = { ...data[index], count: data[index].count + 1 };
  return next;
}
