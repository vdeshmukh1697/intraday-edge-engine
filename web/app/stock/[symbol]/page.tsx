"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  getChart,
  chartWsUrl,
  todayStr,
  type ChartResponse,
  type WsMessage,
} from "@/lib/api";
import CandleChart, { type CandleChartHandle } from "@/components/CandleChart";

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const search = useSearchParams();
  const symbol = decodeURIComponent(
    Array.isArray(params.symbol) ? params.symbol[0] : params.symbol
  );

  const [date, setDate] = useState<string>(search.get("date") || todayStr());
  const [data, setData] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [live, setLive] = useState(false);
  const [liveMsg, setLiveMsg] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const chartHandleRef = useRef<CandleChartHandle | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getChart(symbol, date));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, date]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const stopLive = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setLive(false);
  }, []);

  const startLive = useCallback(() => {
    if (wsRef.current) return;
    try {
      const ws = new WebSocket(chartWsUrl(symbol, date, 0.2));
      wsRef.current = ws;
      setLive(true);
      setLiveMsg("Connecting…");

      ws.onopen = () => setLiveMsg("Streaming");
      ws.onmessage = (ev) => {
        let msg: WsMessage;
        try {
          msg = JSON.parse(ev.data) as WsMessage;
        } catch {
          return;
        }
        if ("done" in msg && msg.done) {
          setLiveMsg("Stream complete");
          stopLive();
          return;
        }
        // It's a bar — append smoothly via series.update.
        chartHandleRef.current?.updateBar(msg);
      };
      ws.onerror = () => setLiveMsg("Connection error");
      ws.onclose = () => {
        wsRef.current = null;
        setLive(false);
      };
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : "Failed to open stream");
      setLive(false);
    }
  }, [symbol, date, stopLive]);

  // Tear down the socket on unmount.
  useEffect(() => stopLive, [stopLive]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>{symbol}</h1>
          <div className="subtle">
            Intraday candles with VWAP / EMA overlays
          </div>
        </div>
        <Link href="/" className="nav-link">
          ← Leaderboard
        </Link>
      </div>

      <form
        className="controls"
        onSubmit={(e) => {
          e.preventDefault();
          stopLive();
          load();
        }}
      >
        <div className="field">
          <label>Date</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Load"}
        </button>
        {!live ? (
          <button
            type="button"
            className="secondary"
            onClick={startLive}
            disabled={!data || loading}
          >
            Go live
          </button>
        ) : (
          <button type="button" className="secondary" onClick={stopLive}>
            Stop
          </button>
        )}
        <span className={`live-status${live ? " on" : ""}`}>
          <span className="dot" />
          {live ? liveMsg || "Streaming" : liveMsg || "Idle"}
        </span>
      </form>

      {error && <div className="notice error">Failed to load: {error}</div>}

      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "#d29922" }} />
          VWAP
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "#4493f8" }} />
          EMA fast
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "#a371f7" }} />
          EMA slow
        </span>
      </div>

      {data ? (
        <CandleChart
          key={`${symbol}-${date}`}
          candles={data.candles}
          vwap={data.overlays.vwap}
          emaFast={data.overlays.ema_fast}
          emaSlow={data.overlays.ema_slow}
          onReady={(h) => {
            chartHandleRef.current = h;
          }}
        />
      ) : (
        !loading &&
        !error && <div className="notice">No chart data loaded.</div>
      )}
    </div>
  );
}
