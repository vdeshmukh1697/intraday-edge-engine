"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { InfoTip } from "@/components/InfoTip";
import {
  getWatchlist,
  getWatchlistQuotes,
  type WatchlistResponse,
  type WatchlistQuote,
} from "@/lib/api";

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const cls = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
// Compact Indian-style traded-volume: 1.2Cr / 3.4L / 5.6K.
const compactVol = (n: number | null | undefined) => {
  if (n == null) return "—";
  if (n >= 1e7) return `${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(2)}L`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
};
const REFRESH_MS = 15000; // watchlist metadata (sector, trades, open positions) — slow poll
const QUOTES_MS = 2000; // live price + volume — fast poll, meets the <=2s target

type QuoteMeta = { source: string; stale: boolean; market_state: string };

export default function WatchlistPage() {
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  // Live quotes live in their OWN state map keyed by symbol, merged at render time — never merged
  // into `data`, so the fast 2s tick can't reshuffle row order (order is driven by `data` only).
  const [quotes, setQuotes] = useState<Record<string, WatchlistQuote>>({});
  const [qmeta, setQmeta] = useState<QuoteMeta | null>(null);

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

  const loadQuotes = useCallback(() => {
    getWatchlistQuotes()
      .then((q) => {
        const map: Record<string, WatchlistQuote> = {};
        for (const s of q.symbols) map[s.symbol] = s;
        setQuotes(map);
        setQmeta({ source: q.source, stale: q.stale, market_state: q.market_state });
      })
      .catch(() => {
        /* quotes are best-effort; the metadata table still renders without them */
      });
  }, []);

  useEffect(() => { load(); loadQuotes(); }, [load, loadQuotes]);
  useEffect(() => {
    if (!auto) return;
    const slow = setInterval(() => load(false), REFRESH_MS);
    const fast = setInterval(loadQuotes, QUOTES_MS);
    return () => { clearInterval(slow); clearInterval(fast); };
  }, [auto, load, loadQuotes]);

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
        {qmeta && (
          <span className={`tag small ${qmeta.source === "LIVE" && !qmeta.stale ? "pos" : ""}`}>
            {qmeta.stale
              ? (qmeta.market_state === "OPEN" || qmeta.market_state === "SQUARE_OFF"
                  ? "FEED STALE"
                  : "MARKET CLOSED")
              : qmeta.source}
          </span>
        )}
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
              <th className="num">Live ₹<InfoTip full="Live price" def="Last traded price from the live feed, refreshed ~2s. Change % is versus the prior session close. Dimmed when the market is closed or the feed is stale." /></th>
              <th className="num">Volume<InfoTip full="Traded volume" def="Cumulative shares traded today on this name, straight from the live feed." /></th>
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
              const q = quotes[r.symbol];
              const chg = q?.change_pct;
              return (
                <tr key={r.symbol} className={op ? "active-row" : ""}>
                  <td className="mono">
                    <Link href={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link>
                  </td>
                  <td className={`num ${q && !q.stale ? cls(chg ?? 0) : ""}`}>
                    {q?.ltp != null ? (
                      <span className={q.stale ? "muted" : ""}>
                        {q.ltp.toFixed(2)}
                        {chg != null && (
                          <span className="small"> ({chg >= 0 ? "+" : ""}{chg.toFixed(2)}%)</span>
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num muted">{compactVol(q?.volume)}</td>
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
