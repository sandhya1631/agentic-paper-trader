/** Shared scaffold placeholder for routes whose features arrive in later stories. */
export function PagePlaceholder({
  title,
  description,
  issue,
}: {
  title: string;
  description: string;
  issue?: string;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-sm text-muted">{description}</p>
      </div>
      <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
        UI scaffold{issue ? ` — planned in ${issue}` : " — coming soon"}
      </div>
    </div>
  );
}
