"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { InfoTip } from "@/components/InfoTip";
import { Sparkline } from "@/components/Sparkline";
import { getWatchlist, quotesWsUrl, type QuotesMessage, type WatchlistResponse } from "@/lib/api";

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const cls = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
const REFRESH_MS = 15000;
const SPARK_POINTS = 40; // rolling LTP buffer length per symbol for the sparkline

export default function WatchlistPage() {
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  // Live quotes (WebSocket): latest LTP per symbol + a rolling buffer for the sparkline.
  const [quotes, setQuotes] = useState<Record<string, number>>({});
  const [buffers, setBuffers] = useState<Record<string, number[]>>({});
  const [live, setLive] = useState(false);

  const load = useCallback((spinner = true) => {
    if (spinner) setLoading(true);
    setError(null);
    getWatchlist()
      .then((d) => {
        setData(d);
        setLastRefresh(new Date());
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [auto, load]);

  // Live LTP stream over WebSocket — updates every ~1s; auto-reconnects on drop.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      try {
        ws = new WebSocket(quotesWsUrl(1));
      } catch {
        return;
      }
      ws.onopen = () => setLive(true);
      ws.onmessage = (ev) => {
        let msg: QuotesMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (!msg.quotes) return;
        const q = msg.quotes;
        setQuotes((prev) => ({ ...prev, ...q }));
        setBuffers((prev) => {
          const next = { ...prev };
          for (const [sym, px] of Object.entries(q)) {
            const arr = (next[sym] || []).concat(px);
            next[sym] = arr.length > SPARK_POINTS ? arr.slice(-SPARK_POINTS) : arr;
          }
          return next;
        });
      };
      ws.onclose = () => {
        setLive(false);
        if (!stopped) retry = setTimeout(connect, 3000);
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error)
    return <div className="card">Could not load the watchlist: {error}</div>;
  if (!data) return <div className="card">Loading…</div>;

  // Open positions first, then traded-today, then the rest; alphabetical within each group.
  const rank = (r: WatchlistResponse["symbols"][number]) =>
    r.open_position ? 0 : r.trades_today > 0 ? 1 : 2;
  const rows = [...data.symbols].sort((a, b) =>
    rank(a) !== rank(b) ? rank(a) - rank(b) : a.symbol.localeCompare(b.symbol)
  );

  return (
    <div className="watchlist">
      <div className="page-head">
        <h1>Watchlist</h1>
        <p className="muted">
          The fixed intraday paper-trading basket — {data.count} liquid, sector-diversified
          NSE names. The live feed subscribes to exactly these and paper-trades signals on them
          through the session. Click any row for that stock&apos;s chart + full paper-trade
          history. Decision-support only — no live orders.
        </p>
      </div>

      <div className="stats-strip">
        <Stat label="Symbols watched" value={String(data.count)} />
        <Stat label="Open now" value={String(data.open_now)} />
        <Stat label="Traded today" value={String(data.traded_today)} />
        <Stat label="Session date" value={data.date} />
        <span
          className={`tag small ${live ? "pos" : ""}`}
          title="Live LTP stream (WebSocket). Ticks during market hours; holds last price when closed."
        >
          {live ? "● LIVE" : "○ offline"}
        </span>
        <span className="live-spacer" />
        {lastRefresh && (
          <span className="muted small">refreshed {lastRefresh.toLocaleTimeString("en-IN")}</span>
        )}
        <label className="toggle small">
          <input type="checkbox" checked={auto} onChange={() => setAuto((a) => !a)} /> auto
        </label>
        <button className="ghost" onClick={() => load(true)} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      <div className="card">
        <table className="grid watchlist-grid">
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Live ₹<InfoTip full="Live price" def="Last traded price, streamed ~1s from the Dhan feed. Ticks during market hours; holds the last traded price when the market is closed." /></th>
              <th>Trend<InfoTip full="Intraday trend" def="Sparkline of the recent live prices this session (rolling ~40 samples). Green = up over the window, red = down." /></th>
              <th>Sector / note<InfoTip term="sector" /></th>
              <th>Status<InfoTip term="direction" /></th>
              <th className="num">Entry ₹<InfoTip term="entry" /></th>
              <th className="num">Target (₹ / %)<InfoTip term="target" /></th>
              <th className="num">Stop (₹ / %)<InfoTip term="stop" /></th>
              <th className="num">Unrealized<InfoTip term="unrealized_pnl" /></th>
              <th className="num">Today<InfoTip full="Today's activity" def="Number of paper trades on this name today and their net ₹ P&L." /></th>
              <th className="num">All-time<InfoTip full="All-time trades" def="Total paper trades recorded on this name across all sessions." /></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const op = r.open_position;
              const ltp = quotes[r.symbol];
              const buf = buffers[r.symbol];
              const prev = buf && buf.length >= 2 ? buf[buf.length - 2] : undefined;
              const tickCls =
                ltp != null && prev != null
                  ? ltp > prev ? "pos" : ltp < prev ? "neg" : ""
                  : "";
              return (
                <tr key={r.symbol} className={op ? "active-row" : ""}>
                  <td className="mono">
                    <Link href={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link>
                  </td>
                  <td className={`num mono ${tickCls}`}>
                    {ltp != null ? ltp.toFixed(2) : "—"}
                  </td>
                  <td>
                    <Sparkline points={buffers[r.symbol] || []} />
                  </td>
                  <td className="muted">{r.sector || "—"}</td>
                  <td>
                    {op ? (
                      <span className={`tag ${op.direction === "LONG" ? "pos" : "neg"}`}>
                        {op.direction} OPEN
                      </span>
                    ) : r.trades_today > 0 ? (
                      <span className="muted small">flat (traded)</span>
                    ) : (
                      <span className="muted small">—</span>
                    )}
                  </td>
                  <td className="num">{op?.entry != null ? op.entry.toFixed(2) : "—"}</td>
                  <td className="num">
                    {op?.target != null ? (
                      <>
                        {op.target.toFixed(2)}
                        {op.target_pct != null && (
                          <span className="muted small"> ({op.target_pct >= 0 ? "+" : ""}{op.target_pct.toFixed(2)}%)</span>
                        )}
                      </>
                    ) : "—"}
                  </td>
                  <td className="num">
                    {op?.stop_loss != null ? (
                      <>
                        {op.stop_loss.toFixed(2)}
                        {op.stop_pct != null && (
                          <span className="muted small"> (-{op.stop_pct.toFixed(2)}%)</span>
                        )}
                      </>
                    ) : "—"}
                  </td>
                  <td className={`num ${cls(op?.unrealized_pnl_pct || 0)}`}>
                    {op?.unrealized_pnl_pct != null
                      ? `${op.unrealized_pnl_pct >= 0 ? "+" : ""}${op.unrealized_pnl_pct.toFixed(2)}%`
                      : "—"}
                  </td>
                  <td className={`num ${cls(r.pnl_today)}`}>
                    {r.trades_today > 0 ? `${r.trades_today} · ${inr(r.pnl_today)}` : "—"}
                  </td>
                  <td className="num">{r.trades_total || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Entry / Target / Stop show only while a position is open (the live trade levels). Target
        &amp; Stop are shown as price (₹) and move (%). The strategy is selective, so most names
        sit flat most of the time.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}
