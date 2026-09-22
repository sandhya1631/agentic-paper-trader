import type { IconName } from "@/components/icons";

export type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  description: string;
};

/**
 * Primary navigation for the operator console. Routes marked as placeholders
 * are scaffolded here and filled in by later Epic 4 stories.
 */
export const navItems: NavItem[] = [
  { href: "/", label: "Dashboard", icon: "dashboard", description: "Portfolio overview" },
  { href: "/positions", label: "Positions", icon: "positions", description: "Open positions & P&L" },
  { href: "/audit", label: "Audit Trail", icon: "audit", description: "Agent reasoning timeline" },
  { href: "/settings", label: "Settings", icon: "settings", description: "Agent & account config" },
];

/** Resolve the current page title from the active pathname (longest match wins). */
export function titleForPath(pathname: string): string {
  const match = [...navItems]
    .sort((a, b) => b.href.length - a.href.length)
    .find((item) => (item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)));
  return match?.label ?? "Agentic Paper Trader";
}
