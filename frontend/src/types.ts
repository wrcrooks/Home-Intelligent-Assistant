// Mirrors of hia.ingest.store.LatestState / hia.ingest.quality.QualityReport /
// hia.api.live's broadcast message shape (backend/src/hia/api/). Hand-written
// rather than generated from the OpenAPI schema — small, stable shapes, not worth
// the extra tooling for a v1 observability shell.

export interface LatestState {
  entity_id: string;
  state: string | null;
  attributes: Record<string, unknown> | null;
  last_changed: string | null;
  last_updated: string | null;
}

export interface EntityActivity {
  entity_id: string;
  row_count: number;
  first_seen: string;
  last_seen: string;
}

export interface QualityReport {
  generated_at: string;
  total_state_changes: number;
  total_events: number;
  tracked_entities: number;
  stale_entities: EntityActivity[];
  gap_resumption_count: number;
}

export interface HourlyActivity {
  hour: string;
  count: number;
}

// Mirrors hia.provenance.actors.ActorClass — the three classes Layer 3 sorts
// an HA account into (docs/05-provenance.md §4). Never inferred client-side;
// this app only ever displays what the backend already decided or suggested.
export type ActorClass = "human" | "voice_bridge" | "service_account";

// Mirrors hia.api.actors.ActorRow.
export interface ActorRow {
  user_id: string;
  name: string;
  system_generated: boolean;
  event_count: number;
  suggested_class: ActorClass | null;
  suggested_reason: string;
  confirmed_class: ActorClass | null;
}

export interface StateChangedMessage {
  type: "state_changed";
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_updated: string;
}
