import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base: SVGProps<SVGSVGElement> = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

/** Small inline SVG icon set (no external icon dependency). */
export const icons = {
  logo: (p: IconProps) => (
    <svg {...base} {...p}>
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M14 7h7v7" />
    </svg>
  ),
  dashboard: (p: IconProps) => (
    <svg {...base} {...p}>
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  ),
  market: (p: IconProps) => (
    <svg {...base} {...p}>
      <path d="M7 4v16M17 4v16" />
      <rect x="4" y="8" width="6" height="7" rx="1" />
      <rect x="14" y="6" width="6" height="9" rx="1" />
    </svg>
  ),
  approvals: (p: IconProps) => (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.5 2.5 4.5-5" />
    </svg>
  ),
  positions: (p: IconProps) => (
    <svg {...base} {...p}>
      <path d="M3 3v18h18" />
      <path d="m19 9-5 5-4-4-4 4" />
    </svg>
  ),
  audit: (p: IconProps) => (
    <svg {...base} {...p}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M8 9h8M8 13h5" />
    </svg>
  ),
  settings: (p: IconProps) => (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
    </svg>
  ),
} as const;

export type IconName = keyof typeof icons;