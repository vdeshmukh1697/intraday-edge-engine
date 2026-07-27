"use client";

import { useEffect, useRef } from "react";
import type {
  IChartApi,
  ISeriesApi,
  LineData,
  UTCTimestamp,
} from "lightweight-charts";
import type { DailyReturn } from "@/lib/api";
import { chartColors, onThemeChange } from "@/lib/theme";

interface Props {
  equityCurve?: number[];
  // Used to derive timestamps for the x-axis when available.
  dailyReturns?: DailyReturn[];
  // Pre-built {time,value} points (epoch seconds, strictly ascending) — takes
  // precedence over equityCurve. Used by /portfolio for intraday snapshots.
  points?: { time: number; value: number }[];
  // Show HH:MM on the x-axis (intraday points); off for daily curves.
  timeVisible?: boolean;
  /** Accessible description of what the curve shows. */
  label: string;
}

// Build {time,value} points for the equity curve. Prefer real dates from
// daily_returns; otherwise fall back to a synthetic daily index so the
// chart still renders a sensible time axis.
function buildPoints(
  equityCurve: number[],
  dailyReturns?: DailyReturn[]
): LineData[] {
  const dayMs = 86400;
  const base = Math.floor(Date.UTC(2020, 0, 1) / 1000);
  return equityCurve.map((value, i) => {
    let t: number;
    const d = dailyReturns?.[i]?.date;
    if (d) {
      const parsed = Math.floor(new Date(`${d}T00:00:00Z`).getTime() / 1000);
      t = Number.isFinite(parsed) ? parsed : base + i * dayMs;
    } else {
      t = base + i * dayMs;
    }
    return { time: t as UTCTimestamp, value };
  });
}

export default function EquityChart({
  equityCurve,
  dailyReturns,
  points,
  timeVisible = false,
  label,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    let chart: IChartApi | null = null;
    let series: ISeriesApi<"Area"> | null = null;
    let resizeObs: ResizeObserver | null = null;
    let unsubscribeTheme: (() => void) | null = null;
    let cancelled = false;

    (async () => {
      const lwc = await import("lightweight-charts");
      if (cancelled || !containerRef.current) return;

      const applyTheme = () => {
        const c = chartColors();
        chart?.applyOptions({
          layout: { background: { color: "transparent" }, textColor: c.text },
          grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
          rightPriceScale: { borderColor: c.axis },
          timeScale: { borderColor: c.axis },
        });
        series?.applyOptions({
          lineColor: c.accent,
          topColor: c.accentFill,
          bottomColor: "transparent",
        });
      };

      chart = lwc.createChart(containerRef.current, {
        layout: { background: { color: "transparent" } },
        timeScale: { timeVisible, secondsVisible: false },
        autoSize: true,
      });
      series = chart.addAreaSeries({ lineWidth: 2, priceLineVisible: false });
      applyTheme();

      const data: LineData[] =
        points && points.length
          ? points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))
          : buildPoints(equityCurve ?? [], dailyReturns);
      series.setData(data);
      chart.timeScale().fitContent();

      resizeObs = new ResizeObserver(() => {
        if (chart && containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      });
      resizeObs.observe(containerRef.current);
      unsubscribeTheme = onThemeChange(applyTheme);
    })();

    return () => {
      cancelled = true;
      unsubscribeTheme?.();
      resizeObs?.disconnect();
      chart?.remove();
    };
  }, [equityCurve, dailyReturns, points, timeVisible]);

  return (
    <div
      ref={containerRef}
      className="chart-frame"
      role="img"
      aria-label={label}
    />
  );
}
