"use client";

import { useEffect, useState } from "react";
import { getWatchlist, type WatchlistResponse } from "@/lib/api";

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const cls = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

export default function WatchlistPage() {
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    setError(null);
    getWatchlist()
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  if (error)
    return <div className="card">Could not load the watchlist: {error}</div>;
  if (!data) return <div className="card">Loading…</div>;

  // Traded-today names first, then the rest; both alphabetical within group.
  const rows = [...data.symbols].sort((a, b) => {
    if ((b.trades_today > 0 ? 1 : 0) !== (a.trades_today > 0 ? 1 : 0))
      return (b.trades_today > 0 ? 1 : 0) - (a.trades_today > 0 ? 1 : 0);
    return a.symbol.localeCompare(b.symbol);
  });

  return (
    <div className="watchlist">
      <div className="page-head">
        <h1>Watchlist</h1>
        <p className="muted">
          The fixed intraday paper-trading basket — {data.count} liquid, sector-diversified
          NSE names (mega-caps, low-priced high-volume, and high-movers). The live feed
          subscribes to exactly these symbols and paper-trades signals on them through the
          session. Decision-support only — no live orders.
        </p>
      </div>

      <div className="stats-strip">
        <Stat label="Symbols watched" value={String(data.count)} />
        <Stat label="Traded today" value={String(data.traded_today)} />
        <Stat label="Session date" value={data.date} />
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {data.traded_today === 0 && (
        <div className="card empty">
          No names have triggered a paper trade yet today. The strategy is deliberately
          selective (high-conviction setups only), so entries are sparse — they appear here and
          in the Paper Trading tab as the session progresses.
        </div>
      )}

      <div className="card">
        <table className="grid">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Sector / note</th>
              <th className="num">Trades today</th>
              <th className="num">P&amp;L today</th>
              <th className="num">Trades (all-time)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className={r.trades_today > 0 ? "active-row" : ""}>
                <td className="mono">{r.symbol}</td>
                <td className="muted">{r.sector || "—"}</td>
                <td className="num">{r.trades_today || "—"}</td>
                <td className={`num ${cls(r.pnl_today)}`}>
                  {r.trades_today > 0 ? inr(r.pnl_today) : "—"}
                </td>
                <td className="num">{r.trades_total || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
