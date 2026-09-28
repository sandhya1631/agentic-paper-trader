"use client";

import { usePathname, useRouter } from "next/navigation";
import { titleForPath } from "@/lib/nav";
import { logout } from "@/lib/auth/auth-api";

/**
 * Top navigation header. Shows the active section title and a compact,
 * read-only agent/account status area (live controls arrive in later stories).
 */
export function Header() {
  const pathname = usePathname();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.replace("/login");
  }

  return (
    <header className="sticky top-0 z-10 flex h-16 items-center justify-between gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur md:px-6">
      <h1 className="truncate text-base font-semibold">{titleForPath(pathname)}</h1>

      <div className="flex items-center gap-3">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-muted px-3 py-1 text-xs font-medium text-muted">
          <span className="h-2 w-2 rounded-full bg-positive" aria-hidden />
          Agent idle
        </span>
        <span className="hidden rounded-full border border-border px-3 py-1 text-xs font-medium text-muted sm:inline">
          Paper account
        </span>
        <button
          type="button"
          onClick={handleLogout}
          className="rounded-full border border-border px-3 py-1 text-xs font-medium text-muted hover:bg-surface-muted hover:text-foreground"
        >
          Log out
        </button>
        <div className="h-8 w-8 rounded-full bg-surface-muted" aria-hidden />
      </div>
    </header>
  );
}
