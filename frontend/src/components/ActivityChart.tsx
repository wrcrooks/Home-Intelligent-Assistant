import type { HourlyActivity } from "../types";

interface ActivityChartProps {
  data: HourlyActivity[] | null;
}

function hourLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric" });
}

function fullLabel(iso: string, count: number): string {
  const d = new Date(iso);
  const when = Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  return `${count} state change${count === 1 ? "" : "s"} — ${when}`;
}

/** A bar per hour, tallest bar scaled to the full available height. Every bar
 * (including zero-count ones) renders with a visible minimum so an empty hour
 * reads as "nothing happened", not as missing data. */
export function ActivityChart({ data }: ActivityChartProps) {
  if (!data) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
        {"Loading…"}
      </p>
    );
  }

  const max = Math.max(1, ...data.map((b) => b.count));

  return (
    <div>
      <h3 className="text-sm font-medium mb-2">State changes, past 24 hours</h3>
      <div className="flex items-end gap-0.5 h-32 border-b border-gray-200 dark:border-gray-700">
        {data.map((bucket) => (
          <div
            key={bucket.hour}
            title={fullLabel(bucket.hour, bucket.count)}
            className="flex-1 min-w-0 bg-sky-500/70 hover:bg-sky-500 dark:bg-sky-400/70 dark:hover:bg-sky-400 rounded-t-sm transition-colors"
            style={{ height: `${Math.max(2, (bucket.count / max) * 100)}%` }}
          />
        ))}
      </div>
      <div className="flex gap-0.5 mt-1">
        {data.map((bucket, i) => (
          <div
            key={bucket.hour}
            className="flex-1 min-w-0 text-center text-[10px] text-gray-400 dark:text-gray-500"
          >
            {i % 4 === 0 ? hourLabel(bucket.hour) : ""}
          </div>
        ))}
      </div>
    </div>
  );
}
