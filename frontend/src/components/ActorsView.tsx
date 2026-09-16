import { useState } from "react";
import { confirmActor } from "../api";
import type { ActorClass, ActorRow } from "../types";

interface ActorsViewProps {
  actors: ActorRow[] | null;
  error: string | null;
  onConfirmed: (userId: string, actorClass: ActorClass) => void;
}

const CLASS_LABELS: Record<ActorClass, string> = {
  human: "Human",
  voice_bridge: "Voice bridge",
  service_account: "Service account",
};

const CLASS_BADGE_CLASSES: Record<ActorClass, string> = {
  human: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  voice_bridge: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  service_account: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
};

function ClassBadge({ actorClass }: { actorClass: ActorClass }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs ${CLASS_BADGE_CLASSES[actorClass]}`}
    >
      {CLASS_LABELS[actorClass]}
    </span>
  );
}

function ActorRowView({
  actor,
  onConfirmed,
}: {
  actor: ActorRow;
  onConfirmed: (userId: string, actorClass: ActorClass) => void;
}) {
  const [pending, setPending] = useState<ActorClass | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  async function confirm(actorClass: ActorClass): Promise<void> {
    setPending(actorClass);
    setRowError(null);
    try {
      await confirmActor(actor.user_id, actorClass);
      onConfirmed(actor.user_id, actorClass);
    } catch (err) {
      setRowError(String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <tr className="border-b border-gray-100 dark:border-gray-800 last:border-0 align-top">
      <td className="py-2 pr-4">
        <div>{actor.name}</div>
        {actor.system_generated && (
          <div className="text-[10px] text-gray-400 dark:text-gray-500">system-generated</div>
        )}
        <div className="font-mono text-[10px] text-gray-400 dark:text-gray-500">
          {actor.user_id}
        </div>
      </td>
      <td className="py-2 pr-4 text-gray-500 dark:text-gray-400">{actor.event_count}</td>
      <td className="py-2 pr-4">
        {actor.suggested_class ? (
          <ClassBadge actorClass={actor.suggested_class} />
        ) : (
          <span className="text-gray-400 dark:text-gray-500">—</span>
        )}
        <div className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5 max-w-xs">
          {actor.suggested_reason}
        </div>
      </td>
      <td className="py-2 pr-4">
        {actor.confirmed_class ? (
          <ClassBadge actorClass={actor.confirmed_class} />
        ) : (
          <span className="text-gray-400 dark:text-gray-500">Not confirmed</span>
        )}
      </td>
      <td className="py-2">
        <div className="flex flex-wrap gap-1">
          {(Object.keys(CLASS_LABELS) as ActorClass[]).map((actorClass) => {
            const isConfirmed = actor.confirmed_class === actorClass;
            return (
              <button
                key={actorClass}
                type="button"
                disabled={pending !== null || isConfirmed}
                onClick={() => void confirm(actorClass)}
                className={`px-2 py-1 text-xs rounded border ${
                  isConfirmed
                    ? "border-gray-200 dark:border-gray-700 text-gray-400 dark:text-gray-600 cursor-default"
                    : "border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
                }`}
              >
                {pending === actorClass ? "Saving…" : CLASS_LABELS[actorClass]}
              </button>
            );
          })}
        </div>
        {rowError && (
          <p className="text-red-600 dark:text-red-400 text-xs mt-1">{rowError}</p>
        )}
      </td>
    </tr>
  );
}

export function ActorsView({ actors, error, onConfirmed }: ActorsViewProps) {
  if (error) {
    return <p className="text-red-600 dark:text-red-400 text-sm py-8 text-center">{error}</p>;
  }
  if (!actors) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
        {"Loading…"}
      </p>
    );
  }
  if (actors.length === 0) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
        No Home Assistant accounts found.
      </p>
    );
  }

  return (
    <div>
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
        Who caused what: tag every Home Assistant account as a real person, a
        voice/smart-home bridge (Alexa, Google, HomeKit), or an automation tool
        (Node-RED, AppDaemon) — this is what lets the provenance classifier tell
        a human action from a machine one. A suggestion is never applied on its
        own; nothing here changes until you confirm it.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th className="py-2 pr-4 font-medium">Account</th>
            <th className="py-2 pr-4 font-medium">Events</th>
            <th className="py-2 pr-4 font-medium">Suggestion</th>
            <th className="py-2 pr-4 font-medium">Confirmed</th>
            <th className="py-2 font-medium">Confirm as</th>
          </tr>
        </thead>
        <tbody>
          {actors.map((actor) => (
            <ActorRowView key={actor.user_id} actor={actor} onConfirmed={onConfirmed} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
