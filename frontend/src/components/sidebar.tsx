"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { navItems } from "@/lib/nav";
import { icons } from "@/components/icons";

/**
 * Persistent navigation sidebar. Always visible; collapses to an icon-only
 * rail on small screens and expands to labels from the `md` breakpoint up.
 */
export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 flex h-screen w-16 shrink-0 flex-col border-r border-border bg-surface md:w-60">
      <div className="flex h-16 items-center gap-2 border-b border-border px-3 md:px-5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand text-brand-foreground">
          <icons.logo width={18} height={18} />
        </span>
        <div className="hidden leading-tight md:block">
          <p className="text-sm font-semibold">Paper Trader</p>
          <p className="text-xs text-muted">Agent Console</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 p-2 md:p-3">
        {navItems.map((item) => {
          const Icon = icons[item.icon];
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors justify-center md:justify-start ${
                active
                  ? "bg-brand/10 font-medium text-brand"
                  : "text-muted hover:bg-surface-muted hover:text-foreground"
              }`}
            >
              <Icon />
              <span className="hidden md:inline">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="hidden border-t border-border p-4 md:block">
        <p className="text-xs text-muted">Paper trading · simulated funds</p>
      </div>
    </aside>
  );
}
