import type { LatestState } from "../types";
import { formatRelativeTime } from "../format";

interface EntityListProps {
  entities: LatestState[];
}

export function EntityList({ entities }: EntityListProps) {
  if (entities.length === 0) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
        No entities yet — once hia serve sees a state_changed event, it shows up
        here.
      </p>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
          <th className="py-2 pr-4 font-medium">Entity</th>
          <th className="py-2 pr-4 font-medium">State</th>
          <th className="py-2 font-medium">Last updated</th>
        </tr>
      </thead>
      <tbody>
        {entities.map((entity) => (
          <tr
            key={entity.entity_id}
            className="border-b border-gray-100 dark:border-gray-800 last:border-0"
          >
            <td className="py-2 pr-4 font-mono text-xs text-gray-700 dark:text-gray-300">
              {entity.entity_id}
            </td>
            <td className="py-2 pr-4">
              <span className="inline-block rounded-full bg-gray-100 dark:bg-gray-800 px-2 py-0.5 text-xs">
                {entity.state ?? "—"}
              </span>
            </td>
            <td className="py-2 text-gray-500 dark:text-gray-400">
              {formatRelativeTime(entity.last_updated)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
