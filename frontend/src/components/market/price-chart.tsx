"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { buildChartRows } from "@/lib/market-data/chart-data";
import type { OHLCVBar } from "@/lib/market-data/market-data-api";

const SERIES = [
  { key: "close", label: "Close", color: "var(--chart-close, #2563eb)" },
  { key: "sma20", label: "SMA-20", color: "var(--chart-sma20, #16a34a)" },
  { key: "sma50", label: "SMA-50", color: "var(--chart-sma50, #d97706)" },
] as const;

const DEFAULT_WIDTH = 640;

/** Measure a container's width (SSR- and jsdom-safe: falls back to a fixed width). */
function useMeasuredWidth(fallback = DEFAULT_WIDTH): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(Math.floor(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/**
 * Per-symbol price chart with SMA-20 / SMA-50 overlays and an RSI-14 sub-chart,
 * built from `/market-data/bars`. Indicators are derived client-side for display
 * (see chart-data.ts). The SVG is decorative (`role="img"` with a text summary);
 * a visible legend and caption carry the same information for assistive tech.
 */
export function PriceChart({ symbol, bars }: { symbol: string; bars: OHLCVBar[] }) {
  const rows = useMemo(() => buildChartRows(bars), [bars]);
  const [ref, width] = useMeasuredWidth();

  if (rows.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-border bg-surface text-sm text-muted">
        No price bars available for {symbol}.
      </div>
    );
  }

  const latest = rows[rows.length - 1];
  const summary =
    `${symbol} price chart: ${rows.length} bars, latest close ${latest.close}` +
    (latest.rsi14 != null ? `, RSI-14 ${latest.rsi14.toFixed(1)}` : "");

  return (
    <div className="space-y-2">
      <ul className="flex flex-wrap items-center gap-4 text-xs text-muted" aria-label="Chart series">
        {SERIES.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-3 rounded-sm"
              style={{ backgroundColor: s.color }}
              aria-hidden
            />
            {s.label}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-3 rounded-sm"
            style={{ backgroundColor: "var(--chart-rsi, #7c3aed)" }}
            aria-hidden
          />
          RSI-14 (sub-chart)
        </li>
        <li className="text-muted/70">indicators derived from bars</li>
      </ul>

      <div ref={ref} role="img" aria-label={summary}>
        <LineChart
          width={width}
          height={240}
          data={rows}
          margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border, #e5e7eb)" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} width={56} />
          <Tooltip />
          {SERIES.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              dot={false}
              strokeWidth={s.key === "close" ? 2 : 1.25}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>

        <LineChart
          width={width}
          height={120}
          data={rows}
          margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border, #e5e7eb)" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis domain={[0, 100]} ticks={[0, 30, 50, 70, 100]} tick={{ fontSize: 11 }} width={56} />
          <ReferenceLine y={70} stroke="var(--chart-sma50, #d97706)" strokeDasharray="2 2" />
          <ReferenceLine y={30} stroke="var(--chart-sma20, #16a34a)" strokeDasharray="2 2" />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="rsi14"
            name="RSI-14"
            stroke="var(--chart-rsi, #7c3aed)"
            dot={false}
            strokeWidth={1.5}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </div>
    </div>
  );
}
