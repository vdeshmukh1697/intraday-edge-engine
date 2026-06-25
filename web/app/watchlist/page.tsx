"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getWatchlist, type WatchlistResponse } from "@/lib/api";

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const cls = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
const REFRESH_MS = 15000;

export default function WatchlistPage() {
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

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
              <th>Sector / note</th>
              <th>Status</th>
              <th className="num">Entry ₹</th>
              <th className="num">Target (₹ / %)</th>
              <th className="num">Stop (₹ / %)</th>
              <th className="num">Unrealized</th>
              <th className="num">Today</th>
              <th className="num">All-time</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const op = r.open_position;
              return (
                <tr key={r.symbol} className={op ? "active-row" : ""}>
                  <td className="mono">
                    <Link href={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link>
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
