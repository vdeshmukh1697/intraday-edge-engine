"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import EquityChart from "@/components/EquityChart";
import { InfoTip } from "@/components/InfoTip";
import {
  getPaperTrades,
  getPortfolio,
  getPortfolioEquity,
  getPredictions,
  todayStr,
  type PaperTrade,
  type PortfolioEquityPoint,
  type PortfolioOpenPosition,
  type PortfolioResponse,
  type Prediction,
} from "@/lib/api";
import { inr, inrPrice, inrSigned, pctSigned, signCls } from "@/lib/format";

// The book is live during the session, so poll like the predictions page: a
// cheap 5s page-level interval. Chart/trades/why-lines refresh on the same tick.
const POLL_MS = 5000;

// lightweight-charts renders UTCTimestamps as UTC wall-clock; shift epoch by
// the IST offset so intraday snapshot times read as market hours (09:15–15:30).
const IST_OFFSET_S = 19800;

// Safari rejects "YYYY-MM-DD HH:MM:SS"; normalize to the ISO "T" form.
function parseTs(ts: string): number {
  return Date.parse((ts || "").replace(" ", "T"));
}

// Equity points → strictly-ascending {time,value} pairs for the chart.
// Repository timestamps are IST; a duplicate time would make setData throw,
// so keep the latest snapshot per second.
function chartPoints(points: PortfolioEquityPoint[]): { time: number; value: number }[] {
  const byTime = new Map<number, number>();
  for (const p of points) {
    const ms = parseTs(p.ts);
    if (!Number.isFinite(ms) || p.equity == null) continue;
    byTime.set(Math.floor(ms / 1000) + IST_OFFSET_S, p.equity);
  }
  return [...byTime.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time, value }));
}

// Plain-English "why" for a closed trade, from the predictions log: prefer the
// exit alert's sentence (it explains the ₹ outcome), else the entry's (why the
// trade was taken). Matched by symbol + nearest timestamp; legacy rows without
// reason_plain simply get no line.
const WHY_WINDOW_MS = 20 * 60 * 1000;

function whyFor(t: PaperTrade, preds: Prediction[]): string | null {
  for (const [kind, refTs] of [
    ["exit", t.exit_ts],
    ["entry", t.entry_ts],
  ] as const) {
    const ref = parseTs(refTs);
    if (!Number.isFinite(ref)) continue;
    let best: { plain: string; dist: number } | null = null;
    for (const p of preds) {
      if (p.kind !== kind || p.symbol !== t.symbol || !p.reason_plain) continue;
      const ts = parseTs(p.ts);
      if (!Number.isFinite(ts)) continue;
      const dist = Math.abs(ts - ref);
      if (dist <= WHY_WINDOW_MS && (!best || dist < best.dist)) {
        best = { plain: p.reason_plain, dist };
      }
    }
    if (best) return best.plain;
  }
  return null;
}

export default function PortfolioPage() {
  const [book, setBook] = useState<PortfolioResponse | null>(null);
  const [points, setPoints] = useState<PortfolioEquityPoint[]>([]);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [preds, setPreds] = useState<Prediction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(() => {
    const today = todayStr();
    // The book is the page; the extras (curve, trades, why-lines) degrade
    // quietly to empty so one missing endpoint never blanks the money view.
    Promise.all([
      getPortfolio(),
      getPortfolioEquity(30).catch(() => ({ points: [] as PortfolioEquityPoint[] })),
      getPaperTrades({ start: today, end: today }).catch(() => ({
        notional_per_trade: 0,
        count: 0,
        trades: [] as PaperTrade[],
      })),
      getPredictions({ limit: 300 }).catch(() => ({ count: 0, predictions: [] as Prediction[] })),
    ])
      .then(([b, eq, tr, pr]) => {
        setBook(b);
        setPoints(eq.points);
        setTrades(tr.trades);
        setPreds(pr.predictions);
        setError(null);
        setLastRefresh(new Date());
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => { load(); }, [load]); // initial load
  useEffect(() => {
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  // Only blank the page when we have nothing to show — a transient poll
  // failure keeps the last good book on screen.
  if (error && !book)
    return <div className="card">Could not load the portfolio: {error}</div>;
  if (!book) return <div className="card">Loading…</div>;

  const overallInr = book.equity - book.starting_capital;
  const curve = chartPoints(points);

  return (
    <div className="paper">
      <div className="page-head">
        <h1>Portfolio</h1>
        <p className="muted">
          One shared paper book of {inr(book.starting_capital)}: the live engine debits cash on
          every entry, credits it back with net P&amp;L on every exit (real cost model), and
          predictions are sized against the same book. Simulated money, real prices — no live
          orders, and no claim of edge. Updates every {POLL_MS / 1000}s.
        </p>
      </div>

      {/* Money header — the "how much money do I have" view. */}
      <div className="card account-summary">
        <div className="account-head">
          <div>
            <div className="metric-label">Total value<InfoTip term="book_equity" /></div>
            <div className={`account-value ${signCls(overallInr)}`}>{inr(book.equity)}</div>
            <div className="metric-sub">
              started {inr(book.starting_capital)}{" "}
              <span className={signCls(overallInr)}>
                {overallInr >= 0 ? "▲" : "▼"} {inrSigned(overallInr)} ({pctSigned(book.return_total_pct)})
              </span>
            </div>
          </div>
          <div className="account-roc">
            <div className="metric-label">Overall return</div>
            <div className={`metric-value ${signCls(overallInr)}`}>
              {pctSigned(book.return_total_pct)}
            </div>
          </div>
        </div>
        <div className="cards account-grid">
          <Card
            label="Cash available"
            term="cash_free"
            value={inr(book.cash)}
            sub="free to fund the next entry"
          />
          <Card
            label="Invested now"
            term="invested_now"
            value={inr(book.invested)}
            sub={
              book.open_positions.length
                ? `${book.open_positions.length} open · unrealized ${inrSigned(book.unrealized_pnl_inr)}`
                : "no open positions"
            }
          />
          <Card
            label="Today's P&L"
            term="realized_today"
            value={inrSigned(book.realized_pnl_today_inr)}
            tone={signCls(book.realized_pnl_today_inr)}
            sub={`${book.today.trades} closed (${book.today.wins} wins) · ${pctSigned(book.return_today_pct)} · charges ${inr(book.today.charges_inr)}`}
          />
          <Card
            label="Overall since start"
            value={inrSigned(overallInr)}
            tone={signCls(overallInr)}
            sub={`realized ${inrSigned(book.realized_pnl_total_inr)} · ${pctSigned(book.return_total_pct)}`}
          />
        </div>
      </div>

      {/* Equity curve of the book (intraday marks + end-of-day snapshots). */}
      <div className="card">
        <h3>Equity curve<InfoTip term="equity_curve" /></h3>
        {curve.length >= 2 ? (
          <EquityChart points={curve} timeVisible />
        ) : (
          <div className="muted">
            No equity history yet — snapshots begin once the live engine starts marking the book
            through a session.
          </div>
        )}
      </div>

      <OpenPositions positions={book.open_positions} />

      <TodayTrades trades={trades} preds={preds} />

      <PerStrategy rows={book.per_strategy} />

      <div className="row">
        <p className="muted small" style={{ margin: 0 }}>
          Paper trading — simulated money, real prices, real cost model. No live orders are ever
          placed.
        </p>
        <span className="live-spacer" />
        {lastRefresh && (
          <span className="muted small">
            refreshed {lastRefresh.toLocaleTimeString("en-IN")}
            {book.updated_ts ? ` · book marked ${book.updated_ts.replace("T", " ").slice(11, 19)}` : ""}
          </span>
        )}
      </div>
    </div>
  );
}

function Card({ label, value, sub, tone, term }: {
  label: string; value: string; sub?: string; tone?: string; term?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}{term && <InfoTip term={term} />}</div>
      <div className={`metric-value ${tone || ""}`}>{value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}

// Open positions with the real ₹ the book has riding on each (polled every 5s).
function OpenPositions({ positions }: { positions: PortfolioOpenPosition[] }) {
  if (positions.length === 0) {
    return (
      <div className="card empty small">
        No open positions right now — entries appear here the moment the live engine fills one.
      </div>
    );
  }
  const totUpnl = positions.reduce((a, p) => a + (p.unrealized_pnl_inr || 0), 0);
  return (
    <div className="card">
      <h3>
        Open positions ({positions.length}){" "}
        <span className={signCls(totUpnl)}>· unrealized {inrSigned(totUpnl)}</span>
      </h3>
      <table className="grid">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side<InfoTip term="direction" /></th>
            <th className="num">Qty<InfoTip term="qty" /></th>
            <th className="num">Avg entry<InfoTip term="entry" /></th>
            <th className="num">Live<InfoTip term="ltp" /></th>
            <th className="num">Invested<InfoTip term="invested_now" /></th>
            <th className="num">P&L<InfoTip term="unrealized_pnl" /></th>
            <th className="num">Stop<InfoTip term="stop" /></th>
            <th className="num">Target<InfoTip term="target" /></th>
            <th>Since</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={`${p.symbol}-${p.entry_ts}`} className="active-row">
              <td className="mono">
                <Link href={`/stock/${encodeURIComponent(p.symbol)}`}>{p.symbol}</Link>
              </td>
              <td><span className={`tag ${p.direction === "LONG" ? "pos" : "neg"}`}>{p.direction}</span></td>
              <td className="num mono">{p.qty ?? "—"}</td>
              <td className="num">{inrPrice(p.entry_fill)}</td>
              <td className="num">{inrPrice(p.last_price)}</td>
              <td className="num">{inr(p.notional)}</td>
              <td className={`num ${signCls(p.unrealized_pnl_inr)}`}>
                {inrSigned(p.unrealized_pnl_inr)}
                {p.unrealized_pnl_pct != null && (
                  <span className="small"> ({pctSigned(p.unrealized_pnl_pct)})</span>
                )}
              </td>
              <td className="num">{inrPrice(p.stop_loss)}</td>
              <td className="num">{inrPrice(p.target)}</td>
              <td className="muted small">{p.entry_ts ? p.entry_ts.slice(11, 16) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Today's closed trades with the plain-English "why" under each row.
function TodayTrades({ trades, preds }: { trades: PaperTrade[]; preds: Prediction[] }) {
  if (trades.length === 0) {
    return (
      <div className="card empty small">
        No closed trades yet today — they appear here as the live engine exits positions.
      </div>
    );
  }
  const sorted = [...trades].sort((a, b) => (a.exit_ts < b.exit_ts ? 1 : -1));
  return (
    <div className="card">
      <h3>Today&apos;s closed trades ({trades.length})</h3>
      <table className="tbl">
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">P&L (after charges)</th>
            <th>Exit</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => {
            const real = t.pnl_inr != null;
            const pnl = real ? (t.pnl_inr as number) : t.net_pnl_abs;
            const why = whyFor(t, preds);
            return (
              <TradeRow key={t.id} t={t} real={real} pnl={pnl} why={why} />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TradeRow({ t, real, pnl, why }: {
  t: PaperTrade; real: boolean; pnl: number; why: string | null;
}) {
  return (
    <>
      <tr>
        <td className="mono">{(t.exit_ts || "").replace("T", " ").slice(11, 16)}</td>
        <td>
          <Link href={`/stock/${encodeURIComponent(t.symbol)}`} className="sym-link">{t.symbol}</Link>
        </td>
        <td><span className={`tag small ${t.direction === "LONG" ? "pos" : "neg"}`}>{t.direction}</span></td>
        <td className="num mono">{t.qty}</td>
        <td className={`num mono ${signCls(pnl)}`}>
          {inrSigned(pnl)}
          {real && t.charges_inr != null && (
            <span className="muted small"> (₹{Math.round(t.charges_inr)} charges)</span>
          )}
          {!real && (
            <span className="tag small modeled" title="Recorded before the ₹1L ledger — ₹ figures modeled at a fixed reference notional.">
              modeled<InfoTip term="modeled_row" />
            </span>
          )}
        </td>
        <td><span className="reason-chip">{t.exit_reason}</span></td>
      </tr>
      {why && (
        <tr className="why-row">
          <td colSpan={6}>Why: {why}</td>
        </tr>
      )}
    </>
  );
}

// Per-strategy contribution (only vwap_ema_adx today, but the table is ready
// for more — and makes "which logic made/lost the money" visible at a glance).
function PerStrategy({ rows }: { rows: PortfolioResponse["per_strategy"] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="card">
      <h3>By strategy<InfoTip term="by_strategy" /></h3>
      <table className="tbl">
        <thead>
          <tr>
            <th>Strategy</th>
            <th className="num">Trades</th>
            <th className="num">Net P&L</th>
            <th className="num">Invested now</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy}>
              <td>{r.strategy}</td>
              <td className="num">{r.trades}</td>
              <td className={`num ${signCls(r.pnl_inr)}`}>{inrSigned(r.pnl_inr)}</td>
              <td className="num">{r.invested_now ? inr(r.invested_now) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
