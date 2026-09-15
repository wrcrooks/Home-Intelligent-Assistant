import { describe, expect, it } from "vitest";
import { applyStateChanged } from "./entityStore";
import type { LatestState, StateChangedMessage } from "./types";

function message(entity_id: string, state: string): StateChangedMessage {
  return {
    type: "state_changed",
    entity_id,
    state,
    attributes: {},
    last_updated: "2026-09-15T12:00:00+00:00",
  };
}

describe("applyStateChanged", () => {
  it("inserts a new entity, keeping the list sorted by entity_id", () => {
    const existing: LatestState[] = [
      { entity_id: "light.a", state: "on", attributes: {}, last_changed: null, last_updated: null },
    ];
    const result = applyStateChanged(existing, message("light.b", "off"));
    expect(result.map((e) => e.entity_id)).toEqual(["light.a", "light.b"]);
  });

  it("replaces an existing entity in place rather than duplicating it", () => {
    const existing: LatestState = {
      entity_id: "light.a",
      state: "off",
      attributes: { brightness: 1 },
      last_changed: "2026-01-01T00:00:00+00:00",
      last_updated: "2026-01-01T00:00:00+00:00",
    };
    const result = applyStateChanged([existing], message("light.a", "on"));
    expect(result).toHaveLength(1);
    expect(result[0].state).toBe("on");
    expect(result[0].attributes).toEqual({});
  });

  it("does not fabricate last_changed for a live-applied update", () => {
    const result = applyStateChanged([], message("light.a", "on"));
    expect(result[0].last_changed).toBeNull();
    expect(result[0].last_updated).toBe("2026-09-15T12:00:00+00:00");
  });

  it("leaves unrelated entities untouched", () => {
    const other: LatestState = {
      entity_id: "light.other",
      state: "on",
      attributes: {},
      last_changed: null,
      last_updated: "2026-01-01T00:00:00+00:00",
    };
    const result = applyStateChanged([other], message("light.a", "on"));
    expect(result.find((e) => e.entity_id === "light.other")).toEqual(other);
  });
});
