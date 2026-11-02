import type { Tone } from "@/lib/format";

const valueToneClass: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "",
};

/** A single KPI card (label, big value, optional hint) using theme tokens. */
export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: Tone;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-2 text-2xl font-semibold ${valueToneClass[tone]}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
