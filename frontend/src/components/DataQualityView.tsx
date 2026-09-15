import type { QualityReport } from "../types";
import { formatRelativeTime } from "../format";

interface DataQualityViewProps {
  report: QualityReport | null;
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">{label}</div>
    </div>
  );
}

export function DataQualityView({ report }: DataQualityViewProps) {
  if (!report) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
        {"Loading…"}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="State changes" value={report.total_state_changes} />
        <Stat label="Other events" value={report.total_events} />
        <Stat label="Tracked entities" value={report.tracked_entities} />
        <Stat label="Gap resumptions" value={report.gap_resumption_count} />
      </div>

      <div>
        <h3 className="text-sm font-medium mb-2">
          Stale entities (no update in 24h+)
        </h3>
        {report.stale_entities.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">None.</p>
        ) : (
          <ul className="text-sm space-y-1">
            {report.stale_entities.map((entity) => (
              <li key={entity.entity_id} className="flex justify-between">
                <span className="font-mono text-xs">{entity.entity_id}</span>
                <span className="text-gray-500 dark:text-gray-400">
                  last seen {formatRelativeTime(entity.last_seen)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="text-xs text-gray-400 dark:text-gray-500">
        Generated {formatRelativeTime(report.generated_at)}
      </p>
    </div>
  );
}
