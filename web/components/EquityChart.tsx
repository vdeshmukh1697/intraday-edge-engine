"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, LineData, UTCTimestamp } from "lightweight-charts";
import type { DailyReturn } from "@/lib/api";

interface Props {
  equityCurve?: number[];
  // Used to derive timestamps for the x-axis when available.
  dailyReturns?: DailyReturn[];
  // Pre-built {time,value} points (epoch seconds, strictly ascending) — takes
  // precedence over equityCurve. Used by /portfolio for intraday snapshots.
  points?: { time: number; value: number }[];
  // Show HH:MM on the x-axis (intraday points); off for daily curves.
  timeVisible?: boolean;
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
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    let chart: IChartApi | null = null;
    let resizeObs: ResizeObserver | null = null;
    let cancelled = false;

    (async () => {
      const lwc = await import("lightweight-charts");
      if (cancelled || !containerRef.current) return;

      chart = lwc.createChart(containerRef.current, {
        layout: {
          background: { color: "transparent" },
          textColor: "#9aa7b5",
        },
        grid: {
          vertLines: { color: "#1c2230" },
          horzLines: { color: "#1c2230" },
        },
        rightPriceScale: { borderColor: "#2a3140" },
        timeScale: {
          borderColor: "#2a3140",
          timeVisible,
          secondsVisible: false,
        },
        autoSize: true,
      });

      const series = chart.addAreaSeries({
        lineColor: "#4493f8",
        topColor: "rgba(68, 147, 248, 0.4)",
        bottomColor: "rgba(68, 147, 248, 0.02)",
        lineWidth: 2,
        priceLineVisible: false,
      });
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
    })();

    return () => {
      cancelled = true;
      resizeObs?.disconnect();
      chart?.remove();
    };
  }, [equityCurve, dailyReturns, points, timeVisible]);

  return <div ref={containerRef} className="equity-box" />;
}
