"use client";

import { useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { getIntraday, quotesWsUrl, type QuotesMessage } from "@/lib/api";

// Always-on live price graph for ONE symbol. On mount it SEEDS the line with today's intraday
// history (from the 09:15 open to now, via /api/intraday) so the chart shows the whole day — not
// just from when the page opened — then appends live /ws/quotes ticks (~1s), keeping timestamps
// strictly monotonic when stitching history + live. Auto-reconnects. Ticks during market hours;
// holds the last traded price (flat line) when the market is closed.
export default function LiveChart({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lastTimeRef = useRef<number>(0);
  const [chartReady, setChartReady] = useState(false);
  const [live, setLive] = useState(false);
  const [ltp, setLtp] = useState<number | null>(null);
  const [seededFrom, setSeededFrom] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Create the chart + line series once (client-only).
  useEffect(() => {
    let chart: IChartApi | null = null;
    let resizeObs: ResizeObserver | null = null;
    let cancelled = false;
    (async () => {
      const lwc = await import("lightweight-charts");
      if (cancelled || !containerRef.current) return;
      chart = lwc.createChart(containerRef.current, {
        layout: { background: { color: "transparent" }, textColor: "#9aa7b5" },
        grid: { vertLines: { color: "#1c2230" }, horzLines: { color: "#1c2230" } },
        rightPriceScale: { borderColor: "#2a3140" },
        timeScale: { borderColor: "#2a3140", timeVisible: true, secondsVisible: true },
        crosshair: { mode: lwc.CrosshairMode.Normal },
        autoSize: true,
      });
      seriesRef.current = chart.addLineSeries({
        color: "#4493f8",
        lineWidth: 2,
        priceLineVisible: true,
        lastValueVisible: true,
      });
      resizeObs = new ResizeObserver(() => {
        if (chart && containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      resizeObs.observe(containerRef.current);
      setChartReady(true);
    })();
    return () => {
      cancelled = true;
      resizeObs?.disconnect();
      chart?.remove();
      seriesRef.current = null;
      setChartReady(false);
    };
  }, []);

  // Seed today's history, THEN stream live ticks. Co-located so the seed is applied before any
  // live point — avoids a setData() wiping early live ticks. Re-runs per symbol / once chart ready.
  useEffect(() => {
    if (!chartReady) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    lastTimeRef.current = 0;
    setSeededFrom(null);

    const applyTick = (px: number, ts: number) => {
      // lightweight-charts needs strictly-increasing time; bump if a tick lands in the same second
      // or before the last seeded bar (history+live stitch must stay monotonic).
      const t = ts <= lastTimeRef.current ? lastTimeRef.current + 1 : ts;
      lastTimeRef.current = t;
      seriesRef.current?.update({ time: t as UTCTimestamp, value: px });
      setLtp(px);
    };

    const connect = () => {
      try {
        ws = new WebSocket(quotesWsUrl(1, [symbol]));
      } catch {
        return;
      }
      ws.onopen = () => { setLive(true); setErr(null); };
      ws.onmessage = (ev) => {
        let m: QuotesMessage;
        try {
          m = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (m.error) { setErr(m.error); return; }
        const px = m.quotes?.[symbol];
        if (px == null || !m.ts) return;
        applyTick(px, m.ts);
      };
      ws.onclose = () => { setLive(false); if (!stopped) retry = setTimeout(connect, 3000); };
      ws.onerror = () => { try { ws?.close(); } catch { /* ignore */ } };
    };

    (async () => {
      // 1) Seed with today's intraday history (best-effort; the live stream still works without it).
      try {
        const hist = await getIntraday(symbol);
        if (!stopped && hist.points.length && seriesRef.current) {
          const pts = [...hist.points]
            .sort((a, b) => a.time - b.time)
            .filter((p, i, arr) => i === 0 || p.time !== arr[i - 1].time); // strictly-increasing
          seriesRef.current.setData(
            pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))
          );
          lastTimeRef.current = pts[pts.length - 1].time;
          setLtp(pts[pts.length - 1].value);
          setSeededFrom(hist.source);
        }
      } catch {
        /* seeding is best-effort — fall straight through to the live stream */
      }
      // 2) Then start the live stream (appends past the last seeded bar).
      if (!stopped) connect();
    })();

    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      try { ws?.close(); } catch { /* ignore */ }
    };
  }, [symbol, chartReady]);

  return (
    <div className="card">
      <div className="chart-head" style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <strong>Live price</strong>
        <span className={`live-status${live ? " on" : ""}`}>
          <span className="dot" /> {live ? "Live" : err ? "offline" : "connecting…"}
        </span>
        {ltp != null && <span className="mono" style={{ marginLeft: "auto", fontSize: 18 }}>₹{ltp.toFixed(2)}</span>}
      </div>
      <div ref={containerRef} className="chart-box" />
      {err && <div className="muted small">{err}</div>}
      <div className="muted small">
        {seededFrom && seededFrom !== "none"
          ? `Seeded with today's intraday history (${seededFrom}), then streaming ~1s live. `
          : "Streams ~1s from the Dhan feed. "}
        Ticks during market hours (09:15–15:30 IST); holds the last traded price when the market is closed.
      </div>
    </div>
  );
}
