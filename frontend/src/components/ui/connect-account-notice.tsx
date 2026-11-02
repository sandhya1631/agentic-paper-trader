import Link from "next/link";

/** Shown when the user has no Alpaca paper account connected yet. */
export function ConnectAccountNotice() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-surface p-8 text-center">
      <p className="text-sm font-medium">No Alpaca paper account connected</p>
      <p className="max-w-md text-sm text-muted">
        Connect your Alpaca paper-trading credentials to see live account data, positions,
        and let the agent place paper orders.
      </p>
      <Link
        href="/settings"
        className="mt-1 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
      >
        Connect account in Settings
      </Link>
    </div>
  );
}
