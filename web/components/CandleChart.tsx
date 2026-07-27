"use client";

import { useEffect, useRef } from "react";
import type {
  IChartApi,
  ISeriesApi,
  CandlestickData,
  HistogramData,
  LineData,
  UTCTimestamp,
} from "lightweight-charts";
import type { Candle, LinePoint } from "@/lib/api";
import { chartColors, onThemeChange } from "@/lib/theme";

export interface CandleChartHandle {
  updateBar: (bar: Candle) => void;
}

interface Props {
  candles: Candle[];
  vwap: LinePoint[];
  emaFast: LinePoint[];
  emaSlow: LinePoint[];
  // Receives a handle exposing series.update for live streaming.
  onReady?: (handle: CandleChartHandle) => void;
}

export default function CandleChart({
  candles,
  vwap,
  emaFast,
  emaSlow,
  onReady,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  // Keep the latest onReady without re-creating the chart.
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // Create chart + series once, client-side only.
  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    let chart: IChartApi | null = null;
    let resizeObs: ResizeObserver | null = null;
    let unsubscribeTheme: (() => void) | null = null;
    let cancelled = false;

    (async () => {
      const lwc = await import("lightweight-charts");
      if (cancelled || !containerRef.current) return;

      chart = lwc.createChart(containerRef.current, {
        layout: { background: { color: "transparent" } },
        timeScale: { timeVisible: true, secondsVisible: false },
        crosshair: { mode: lwc.CrosshairMode.Normal },
        autoSize: true,
      });
      chartRef.current = chart;

      const candleSeries = chart.addCandlestickSeries({});
      candleSeriesRef.current = candleSeries;

      // Traded-volume histogram, pinned to the bottom ~18% on its own overlay price scale so it
      // never distorts the candle axis. Bars are tinted by candle direction.
      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
        color: "#2a3140",
      });
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      volumeSeriesRef.current = volumeSeries;

      const vwapSeries = chart.addLineSeries({
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const fastSeries = chart.addLineSeries({
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const slowSeries = chart.addLineSeries({
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      // Overlay hues come from the token layer, so the chart follows the theme.
      const applyTheme = () => {
        const c = chartColors();
        chart?.applyOptions({
          layout: { background: { color: "transparent" }, textColor: c.text },
          grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
          rightPriceScale: { borderColor: c.axis },
          timeScale: { borderColor: c.axis },
        });
        candleSeries.applyOptions({
          upColor: c.positive,
          downColor: c.negative,
          borderUpColor: c.positive,
          borderDownColor: c.negative,
          wickUpColor: c.positive,
          wickDownColor: c.negative,
        });
        vwapSeries.applyOptions({ color: c.series2 });
        fastSeries.applyOptions({ color: c.accent });
        slowSeries.applyOptions({ color: c.series7 });
      };
      applyTheme();
      unsubscribeTheme = onThemeChange(applyTheme);

      candleSeries.setData(
        candles.map(
          (c): CandlestickData => ({
            time: c.time as UTCTimestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          })
        )
      );
      volumeSeries.setData(
        candles
          .filter((c) => c.volume != null)
          .map(
            (c): HistogramData => ({
              time: c.time as UTCTimestamp,
              value: c.volume as number,
              color: c.close >= c.open ? "rgba(63,185,80,0.45)" : "rgba(248,81,73,0.45)",
            })
          )
      );
      const toLine = (pts: LinePoint[]): LineData[] =>
        pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
      vwapSeries.setData(toLine(vwap));
      fastSeries.setData(toLine(emaFast));
      slowSeries.setData(toLine(emaSlow));

      chart.timeScale().fitContent();

      // Manual resize fallback (autoSize covers most cases).
      resizeObs = new ResizeObserver(() => {
        if (chart && containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      });
      resizeObs.observe(containerRef.current);

      onReadyRef.current?.({
        updateBar: (bar: Candle) => {
          candleSeriesRef.current?.update({
            time: bar.time as UTCTimestamp,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          });
          if (bar.volume != null) {
            volumeSeriesRef.current?.update({
              time: bar.time as UTCTimestamp,
              value: bar.volume,
              color: bar.close >= bar.open ? "rgba(63,185,80,0.45)" : "rgba(248,81,73,0.45)",
            });
          }
        },
      });
    })();

    return () => {
      cancelled = true;
      unsubscribeTheme?.();
      resizeObs?.disconnect();
      chart?.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
    // Re-create only when the static dataset identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, vwap, emaFast, emaSlow]);

  return (
    <div
      ref={containerRef}
      className="chart-frame"
      data-size="tall"
      role="img"
      aria-label={`Candlestick chart with VWAP and EMA overlays, ${candles.length} bars`}
    />
  );
}
