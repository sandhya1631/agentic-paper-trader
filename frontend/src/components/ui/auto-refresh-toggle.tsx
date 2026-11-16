"use client";

/**
 * Small, visible control for the live-refresh state (#143). Shows whether
 * auto-refresh is on and its interval, and lets the operator stop/start it.
 * `updating` pulses the dot while a refresh is in flight (#164), with no layout shift.
 */
export function AutoRefreshToggle({
  enabled,
  onChange,
  seconds,
  updating = false,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  seconds: number;
  updating?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!enabled)}
      aria-pressed={enabled}
      aria-busy={updating || undefined}
      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-surface-muted"
      title={enabled ? "Auto-refresh is on — click to pause" : "Auto-refresh is paused — click to resume"}
    >
      <span
        className={`h-2 w-2 rounded-full ${enabled ? "bg-positive" : "bg-muted"} ${
          updating ? "animate-pulse" : ""
        }`}
        aria-hidden
      />
      {enabled ? `Live · every ${seconds}s` : "Paused"}
    </button>
  );
}
