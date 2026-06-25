"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  getChart,
  chartWsUrl,
  todayStr,
  getPaperTrades,
  getOpenPositions,
  type ChartResponse,
  type WsMessage,
  type LiveBar,
  type PaperTrade,
  type OpenPosition,
} from "@/lib/api";
import CandleChart, { type CandleChartHandle } from "@/components/CandleChart";
import { InfoTip } from "@/components/InfoTip";

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const clsOf = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

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

  // Paper-trade history + current open position for THIS symbol.
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [openPos, setOpenPos] = useState<OpenPosition | null>(null);

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

  // Paper-trade history + open position for this symbol (independent of the chart date).
  const loadHistory = useCallback(async () => {
    try {
      const [t, o] = await Promise.all([
        getPaperTrades({ symbol }),
        getOpenPositions(),
      ]);
      setTrades(t.trades);
      setOpenPos(o.positions.find((p) => p.symbol === symbol) || null);
    } catch {
      /* history is best-effort; the chart still renders */
    }
  }, [symbol]);

  useEffect(() => {
    load();
    loadHistory();
    const id = setInterval(loadHistory, 15000); // keep history/open-position fresh
    return () => clearInterval(id);
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
        chartHandleRef.current?.updateBar(msg as LiveBar);
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
          VWAP<InfoTip term="vwap" />
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "#4493f8" }} />
          EMA fast<InfoTip term="ema" />
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

      {openPos && <OpenPositionCard pos={openPos} />}
      <PaperHistory symbol={symbol} trades={trades} />
    </div>
  );
}

// Current open position for this symbol — live entry/target/stop (₹ + %) + unrealized P&L.
function OpenPositionCard({ pos }: { pos: OpenPosition }) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>
        Open position{" "}
        <span className={`tag ${pos.direction === "LONG" ? "pos" : "neg"}`}>{pos.direction}</span>
      </h3>
      <div className="cards">
        <Metric label="Entry" term="entry" value={`₹${pos.entry?.toFixed(2)}`}
          sub={pos.entry_ts ? `since ${pos.entry_ts.slice(11, 16)}` : undefined} />
        <Metric label="Last price" term="ltp" value={pos.last_price != null ? `₹${pos.last_price.toFixed(2)}` : "—"} />
        <Metric label="Target" term="target"
          value={pos.target != null ? `₹${pos.target.toFixed(2)}` : "—"}
          sub={pos.target_pct != null ? `${pos.target_pct >= 0 ? "+" : ""}${pos.target_pct.toFixed(2)}%` : undefined} />
        <Metric label="Stop" term="stop"
          value={pos.stop_loss != null ? `₹${pos.stop_loss.toFixed(2)}` : "—"}
          sub={pos.stop_pct != null ? `-${pos.stop_pct.toFixed(2)}%` : undefined} />
        <Metric label="Unrealized P&L" term="unrealized_pnl"
          value={pos.unrealized_pnl_pct != null
            ? `${pos.unrealized_pnl_pct >= 0 ? "+" : ""}${pos.unrealized_pnl_pct.toFixed(2)}%`
            : "—"}
          sub={pos.unrealized_pnl_abs != null ? inr(pos.unrealized_pnl_abs) : undefined}
          tone={clsOf(pos.unrealized_pnl_pct || 0)} />
        <Metric label="R:R" term="rr" value={pos.risk_reward != null ? pos.risk_reward.toFixed(2) : "—"}
          sub={`conf ${pos.confidence?.toFixed(0)}`} />
      </div>
    </div>
  );
}

// Full paper-trade history for this symbol.
function PaperHistory({ symbol, trades }: { symbol: string; trades: PaperTrade[] }) {
  const net = trades.reduce((a, t) => a + (t.net_pnl_abs || 0), 0);
  const wins = trades.filter((t) => (t.net_pnl_abs || 0) > 0).length;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>
        Paper-trade history — {symbol} ({trades.length})
        {trades.length > 0 && (
          <span className={clsOf(net)}> · net {inr(net)} · {wins}W/{trades.length - wins}L</span>
        )}
      </h3>
      {trades.length === 0 ? (
        <div className="muted small">
          No paper trades recorded for {symbol} yet. They appear here as the live paper-trader
          fires on this name.
        </div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>Dir<InfoTip term="direction" /></th><th>Entry</th><th className="num">Entry ₹</th>
              <th>Exit</th><th className="num">Exit ₹</th><th>Reason</th>
              <th className="num">Target<InfoTip term="target" /></th><th className="num">Stop<InfoTip term="stop" /></th>
              <th className="num">Net %</th><th className="num">Net ₹</th><th className="num">R<InfoTip term="r_multiple" /></th>
            </tr>
          </thead>
          <tbody>
            {[...trades].reverse().map((t) => (
              <tr key={t.id}>
                <td><span className={`tag ${t.direction === "LONG" ? "pos" : "neg"}`}>{t.direction}</span></td>
                <td className="muted small">{t.entry_ts?.slice(5, 16).replace("T", " ")}</td>
                <td className="num">{t.entry_fill?.toFixed(2)}</td>
                <td className="muted small">{t.exit_ts?.slice(5, 16).replace("T", " ")}</td>
                <td className="num">{t.exit_fill?.toFixed(2)}</td>
                <td className="small">{t.exit_reason}</td>
                <td className="num">{t.target != null ? t.target.toFixed(2) : "—"}</td>
                <td className="num">{t.stop_loss != null ? t.stop_loss.toFixed(2) : "—"}</td>
                <td className={`num ${clsOf(t.net_pnl_pct)}`}>
                  {t.net_pnl_pct >= 0 ? "+" : ""}{t.net_pnl_pct?.toFixed(2)}%
                </td>
                <td className={`num ${clsOf(t.net_pnl_abs)}`}>{inr(t.net_pnl_abs)}</td>
                <td className="num">{t.r_multiple != null ? `${t.r_multiple >= 0 ? "+" : ""}${t.r_multiple.toFixed(2)}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Metric({ label, value, sub, tone, term }: { label: string; value: string; sub?: string; tone?: string; term?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}{term && <InfoTip term={term} />}</div>
      <div className={`metric-value ${tone || ""}`}>{value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}
