const stats = [
  { label: "Portfolio value", value: "—", hint: "Awaiting broker data", tone: "muted" },
  { label: "Cash", value: "—", hint: "Awaiting broker data", tone: "muted" },
  { label: "Open positions", value: "—", hint: "Awaiting broker data", tone: "muted" },
  { label: "Day P&L", value: "—", hint: "Awaiting broker data", tone: "muted" },
] as const;

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <p className="text-sm text-muted">
          Portfolio overview and agent activity. This is the UI scaffold — live
          data and charts are wired up in later Epic&nbsp;4 stories.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="rounded-xl border border-border bg-surface p-5"
          >
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              {stat.label}
            </p>
            <p className="mt-2 text-2xl font-semibold">{stat.value}</p>
            <p className="mt-1 text-xs text-muted">{stat.hint}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-border bg-surface p-5 lg:col-span-2">
          <h3 className="text-sm font-semibold">Equity curve</h3>
          <div className="mt-4 flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
            Chart placeholder
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface p-5">
          <h3 className="text-sm font-semibold">Recent decisions</h3>
          <div className="mt-4 flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
            Timeline placeholder
          </div>
        </div>
      </section>
    </div>
  );
}
