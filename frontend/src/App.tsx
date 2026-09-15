import { useEffect, useState } from "react";
import { connectLiveEvents, fetchDataQuality, fetchEntities } from "./api";
import { applyStateChanged } from "./entityStore";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { EntityList } from "./components/EntityList";
import { DataQualityView } from "./components/DataQualityView";
import type { LatestState, QualityReport } from "./types";

type Tab = "entities" | "data-quality";

const TABS: { id: Tab; label: string }[] = [
  { id: "entities", label: "Entities" },
  { id: "data-quality", label: "Data quality" },
];

const DATA_QUALITY_POLL_MS = 10_000;
// The quality report is not push-driven the way entities are — hia serve's live
// relay only carries state_changed events (docs/HANDOFF.md), so this page polls
// instead. 10s is frequent enough to feel live for a report that summarises
// slow-moving things (totals, staleness), without hammering the API.

export default function App() {
  const [tab, setTab] = useState<Tab>("entities");
  const [entities, setEntities] = useState<LatestState[]>([]);
  const [entitiesError, setEntitiesError] = useState<string | null>(null);
  const [report, setReport] = useState<QualityReport | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    fetchEntities()
      .then(setEntities)
      .catch((err: unknown) => setEntitiesError(String(err)));

    const disconnect = connectLiveEvents(
      (message) => setEntities((prev) => applyStateChanged(prev, message)),
      setConnected,
    );
    return disconnect;
  }, []);

  useEffect(() => {
    let cancelled = false;
    function poll(): void {
      fetchDataQuality()
        .then((r) => {
          if (!cancelled) setReport(r);
        })
        .catch(() => {
          // Transient; the next poll retries. Not surfaced as an error state —
          // a single missed poll isn't worth alarming over.
        });
    }
    poll();
    const interval = setInterval(poll, DATA_QUALITY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="min-h-screen bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100">
      <header className="border-b border-gray-200 dark:border-gray-800 px-4 py-3 flex items-center justify-between">
        <h1 className="text-base font-semibold">Home Intelligent Assistant</h1>
        <ConnectionBadge connected={connected} />
      </header>

      <nav className="flex gap-1 px-4 pt-3">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`px-3 py-1.5 text-sm rounded-t-md ${
              tab === id
                ? "bg-gray-100 dark:bg-gray-900 font-medium"
                : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="p-4">
        {tab === "entities" ? (
          <>
            {entitiesError && (
              <p className="text-red-600 dark:text-red-400 text-sm mb-3">
                {entitiesError}
              </p>
            )}
            <EntityList entities={entities} />
          </>
        ) : (
          <DataQualityView report={report} />
        )}
      </main>
    </div>
  );
}
