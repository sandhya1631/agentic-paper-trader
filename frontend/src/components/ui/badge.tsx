import type { ReactNode } from "react";

export type BadgeTone = "positive" | "negative" | "neutral" | "brand";

const toneClass: Record<BadgeTone, string> = {
  positive: "bg-positive/10 text-positive",
  negative: "bg-negative/10 text-negative",
  neutral: "bg-surface-muted text-muted",
  brand: "bg-brand/10 text-brand",
};

/** Small status/label pill using theme tokens (works in light + dark). */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${toneClass[tone]}`}
    >
      {children}
    </span>
  );
}
