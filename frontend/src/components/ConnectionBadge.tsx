interface ConnectionBadgeProps {
  connected: boolean;
}

export function ConnectionBadge({ connected }: ConnectionBadgeProps) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300">
      <span
        className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`}
        aria-hidden="true"
      />
      {connected ? "Live" : "Reconnecting…"}
    </span>
  );
}
