"use client";

import { useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { quotesWsUrl, type QuotesMessage } from "@/lib/api";

// Always-on live price graph for ONE symbol: opens the /ws/quotes stream (filtered to this
// symbol) on mount and plots last-traded price over time, updating ~1s. Auto-reconnects.
// Ticks during market hours; holds the last traded price (flat line) when the market is closed.
export default function LiveChart({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lastTimeRef = useRef<number>(0);
  const [live, setLive] = useState(false);
  const [ltp, setLtp] = useState<number | null>(null);
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
    })();
    return () => {
      cancelled = true;
      resizeObs?.disconnect();
      chart?.remove();
      seriesRef.current = null;
    };
  }, []);

  // Subscribe to the live LTP stream for this symbol; auto-reconnect on drop.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    lastTimeRef.current = 0;
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
        // lightweight-charts needs strictly-increasing time; bump if a tick lands in the same second.
        let t = m.ts <= lastTimeRef.current ? lastTimeRef.current + 1 : m.ts;
        lastTimeRef.current = t;
        seriesRef.current?.update({ time: t as UTCTimestamp, value: px });
        setLtp(px);
      };
      ws.onclose = () => { setLive(false); if (!stopped) retry = setTimeout(connect, 3000); };
      ws.onerror = () => { try { ws?.close(); } catch { /* ignore */ } };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      try { ws?.close(); } catch { /* ignore */ }
    };
  }, [symbol]);

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
        Streams ~1s from the Dhan feed. Ticks during market hours (09:15–15:30 IST); holds the last
        traded price when the market is closed.
      </div>
    </div>
  );
}
