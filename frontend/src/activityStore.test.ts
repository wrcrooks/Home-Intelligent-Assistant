import { describe, expect, it } from "vitest";
import { applyLiveStateChange } from "./activityStore";
import type { HourlyActivity, StateChangedMessage } from "./types";

function message(last_updated: string): StateChangedMessage {
  return {
    type: "state_changed",
    entity_id: "light.a",
    state: "on",
    attributes: {},
    last_updated,
  };
}

describe("applyLiveStateChange", () => {
  it("increments the bucket the message's hour falls into", () => {
    const data: HourlyActivity[] = [
      { hour: "2026-09-15T11:00:00+00:00", count: 2 },
      { hour: "2026-09-15T12:00:00+00:00", count: 5 },
    ];
    const result = applyLiveStateChange(data, message("2026-09-15T12:34:56+00:00"));
    expect(result[1].count).toBe(6);
    expect(result[0].count).toBe(2); // unrelated bucket untouched
  });

  it("aligns to the UTC hour, not the browser's local hour", () => {
    const data: HourlyActivity[] = [{ hour: "2026-09-15T12:00:00+00:00", count: 0 }];
    // 12:59:59 UTC is still hour 12, regardless of what local offset renders it as.
    const result = applyLiveStateChange(data, message("2026-09-15T12:59:59+00:00"));
    expect(result[0].count).toBe(1);
  });

  it("drops a message whose hour isn't in the current window rather than fabricating a bucket", () => {
    const data: HourlyActivity[] = [{ hour: "2026-09-15T12:00:00+00:00", count: 3 }];
    const result = applyLiveStateChange(data, message("2026-09-16T09:00:00+00:00"));
    expect(result).toEqual(data);
  });

  it("ignores an unparseable timestamp", () => {
    const data: HourlyActivity[] = [{ hour: "2026-09-15T12:00:00+00:00", count: 1 }];
    const result = applyLiveStateChange(data, message("not-a-timestamp"));
    expect(result).toEqual(data);
  });
});
