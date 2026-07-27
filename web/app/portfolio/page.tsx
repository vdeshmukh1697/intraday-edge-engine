"use client";

import { useCallback, useEffect, useState } from "react";
import EquityChart from "@/components/EquityChart";
import { InfoTip } from "@/components/InfoTip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Badge,
  Callout,
  Card,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { Hero, StatTile } from "@/components/ui/stats";
import { DirectionTag, Money, SymbolLink } from "@/components/ui/cells";
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
import {
  clock,
  hhmm,
  hhmmss,
  inr,
  inrSigned,
  parseTs,
  pctSigned,
  signArrow,
  signCls,
} from "@/lib/format";

// The book is live during the session, so poll like the predictions page: a
// cheap 5s page-level interval. Chart/trades/why-lines refresh on the same tick.
const POLL_MS = 5000;

// lightweight-charts renders UTCTimestamps as UTC wall-clock; shift epoch by
// the IST offset so intraday snapshot times read as market hours (09:15–15:30).
const IST_OFFSET_S = 19800;

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
      getPredictions({ limit: 300 }).catch(() => ({
        count: 0,
        predictions: [] as Prediction[],
      })),
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

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  // Only blank the page when we have nothing to show — a transient poll
  // failure keeps the last good book on screen.
  if (error && !book) {
    return (
      <>
        <PageHeader title="Portfolio" />
        <ErrorBanner>Could not load the portfolio: {error}</ErrorBanner>
      </>
    );
  }
  if (!book) {
    return (
      <>
        <PageHeader title="Portfolio" />
        <LoadingBlock label="Loading the paper book" />
      </>
    );
  }

  const overallInr = book.equity - book.starting_capital;
  const curve = chartPoints(points);
  const openUpnl = book.unrealized_pnl_inr;

  return (
    <>
      <PageHeader
        title="Portfolio"
        eyebrow={`Paper book · started ${inr(book.starting_capital)}`}
        lede={
          <>
            One shared paper book. The live engine debits cash on every entry and credits
            it back with net P&amp;L on every exit, using a real cost model; every
            prediction is sized against this same book.{" "}
            <strong>
              Simulated money, real prices — no live orders, and no claim of edge.
            </strong>{" "}
            Refreshes every {POLL_MS / 1000}s.
          </>
        }
        aside={
          <Badge tone="warning" size="lg">
            Paper money
          </Badge>
        }
      />

      {/* Hero: the two questions asked from a phone during market hours — what is
          the book worth, and what has today done to it. */}
      <Card>
        <div className="hero-split">
          <Hero
            label={
              <>
                Total value
                <InfoTip term="book_equity" />
              </>
            }
            value={inr(book.equity)}
            tone={signCls(overallInr)}
          >
            <span className={signCls(overallInr)}>
              <span className="delta-arrow" aria-hidden="true">
                {signArrow(overallInr)}
              </span>{" "}
              {inrSigned(overallInr)} ({pctSigned(book.return_total_pct)})
            </span>
            <span className="faint">since {inr(book.starting_capital)} start</span>
          </Hero>
          <div className="stat-grid">
            <StatTile
              label={
                <>
                  Today&apos;s P&amp;L
                  <InfoTip term="realized_today" />
                </>
              }
              value={inrSigned(book.realized_pnl_today_inr)}
              tone={signCls(book.realized_pnl_today_inr)}
              sub={`${book.today.trades} closed (${book.today.wins} won) · ${pctSigned(
                book.return_today_pct
              )} · charges ${inr(book.today.charges_inr)}`}
            />
            <StatTile
              label={
                <>
                  Cash available
                  <InfoTip term="cash_free" />
                </>
              }
              value={inr(book.cash)}
              sub="free to fund the next entry"
            />
            <StatTile
              label={
                <>
                  Invested now
                  <InfoTip term="invested_now" />
                </>
              }
              value={inr(book.invested)}
              sub={
                book.open_positions.length
                  ? `${book.open_positions.length} open · unrealized ${inrSigned(openUpnl)}`
                  : "no open positions"
              }
            />
            <StatTile
              label="Realized since start"
              value={inrSigned(book.realized_pnl_total_inr)}
              tone={signCls(book.realized_pnl_total_inr)}
              sub={`closed trades only · ${pctSigned(book.return_total_pct)} overall`}
            />
          </div>
        </div>
      </Card>

      <Section
        titleNode={
          <>
            Equity curve
            <InfoTip term="equity_curve" />
          </>
        }
        note="Intraday marks during the session plus one end-of-day snapshot, last 30 days. Simulated book value, not a return you could have banked."
      >
        {curve.length >= 2 ? (
          <EquityChart
            points={curve}
            timeVisible
            label={`Paper book value over the last 30 days, currently ${inr(book.equity)}`}
          />
        ) : (
          <EmptyState title="No equity history yet">
            Snapshots begin once the live engine marks the book through a session.
          </EmptyState>
        )}
      </Section>

      <OpenPositions positions={book.open_positions} />

      <TodayTrades trades={trades} preds={preds} />

      <PerStrategy rows={book.per_strategy} />

      <p className="tiny faint">
        Paper trading — simulated money, real prices, real cost model. No live orders are
        ever placed.
        {lastRefresh && (
          <>
            {" "}
            Page refreshed {clock(lastRefresh)}
            {book.updated_ts ? ` · book marked ${hhmmss(book.updated_ts)}` : ""}.
          </>
        )}
      </p>
    </>
  );
}

// Open positions with the real ₹ the book has riding on each (polled every 5s).
function OpenPositions({ positions }: { positions: PortfolioOpenPosition[] }) {
  const totUpnl = positions.reduce((a, p) => a + (p.unrealized_pnl_inr || 0), 0);

  const columns: Column<PortfolioOpenPosition>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (p) => (
        <>
          <SymbolLink symbol={p.symbol} />
          <span className="stack-only">
            <DirectionTag direction={p.direction} />
          </span>
        </>
      ),
    },
    {
      id: "side",
      header: (
        <>
          Side
          <InfoTip term="direction" />
        </>
      ),
      label: "Side",
      hideOnStack: true,
      cell: (p) => <DirectionTag direction={p.direction} />,
    },
    {
      id: "pnl",
      header: (
        <>
          P&amp;L
          <InfoTip term="unrealized_pnl" />
        </>
      ),
      label: "Unrealized",
      numeric: true,
      cell: (p) => (
        <Money
          value={p.unrealized_pnl_inr}
          kind="signed"
          sub={
            p.unrealized_pnl_pct != null ? (
              <span className="small"> ({pctSigned(p.unrealized_pnl_pct)})</span>
            ) : undefined
          }
        />
      ),
    },
    {
      id: "qty",
      header: (
        <>
          Qty
          <InfoTip term="qty" />
        </>
      ),
      label: "Qty",
      numeric: true,
      cell: (p) => p.qty ?? "—",
    },
    {
      id: "entry",
      header: (
        <>
          Avg entry
          <InfoTip term="entry" />
        </>
      ),
      label: "Avg entry",
      numeric: true,
      cell: (p) => <Money value={p.entry_fill} kind="price" />,
    },
    {
      id: "last",
      header: (
        <>
          Live
          <InfoTip term="ltp" />
        </>
      ),
      label: "Live price",
      numeric: true,
      cell: (p) => <Money value={p.last_price} kind="price" />,
    },
    {
      id: "invested",
      header: (
        <>
          Invested
          <InfoTip term="invested_now" />
        </>
      ),
      label: "Invested",
      numeric: true,
      cell: (p) => <Money value={p.notional} />,
    },
    {
      id: "stop",
      header: (
        <>
          Stop
          <InfoTip term="stop" />
        </>
      ),
      label: "Stop",
      numeric: true,
      cell: (p) => <Money value={p.stop_loss} kind="price" />,
    },
    {
      id: "target",
      header: (
        <>
          Target
          <InfoTip term="target" />
        </>
      ),
      label: "Target",
      numeric: true,
      cell: (p) => <Money value={p.target} kind="price" />,
    },
    {
      id: "since",
      header: "Since",
      label: "Open since",
      cell: (p) => <span className="faint mono">{hhmm(p.entry_ts)}</span>,
    },
  ];

  return (
    <Section
      title={`Open positions (${positions.length})`}
      note="Marked to the latest streamed price. Nothing here is a real order."
      aside={
        positions.length > 0 ? (
          <span className={signCls(totUpnl)}>
            unrealized <strong>{inrSigned(totUpnl)}</strong>
          </span>
        ) : undefined
      }
    >
      <DataTable
        label="Open paper positions"
        columns={columns}
        rows={positions}
        rowKey={(p) => `${p.symbol}-${p.entry_ts}`}
        rowFlag={() => "active"}
        empty={
          <EmptyState title="Nothing open right now">
            Entries appear here the moment the live engine fills one.
          </EmptyState>
        }
      />
    </Section>
  );
}

// Today's closed trades with the plain-English "why" under each row.
function TodayTrades({ trades, preds }: { trades: PaperTrade[]; preds: Prediction[] }) {
  const sorted = [...trades].sort((a, b) => (a.exit_ts < b.exit_ts ? 1 : -1));
  const pnlOf = (t: PaperTrade) => (t.pnl_inr != null ? t.pnl_inr : t.net_pnl_abs);

  const columns: Column<PaperTrade>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (t) => (
        <>
          <SymbolLink symbol={t.symbol} />
          <span className={`stack-only mono ${signCls(pnlOf(t))}`}>
            {inrSigned(pnlOf(t))}
          </span>
        </>
      ),
    },
    {
      id: "time",
      header: "Exit time",
      label: "Exit time",
      cell: (t) => <span className="mono">{hhmm(t.exit_ts)}</span>,
    },
    {
      id: "side",
      header: "Side",
      label: "Side",
      cell: (t) => <DirectionTag direction={t.direction} />,
    },
    {
      id: "qty",
      header: "Qty",
      label: "Qty",
      numeric: true,
      cell: (t) => t.qty,
    },
    {
      id: "pnl",
      header: "P&L (after charges)",
      label: "P&L after charges",
      numeric: true,
      hideOnStack: true,
      cell: (t) => {
        const real = t.pnl_inr != null;
        return (
          <>
            <Money value={pnlOf(t)} kind="signed" />
            {real && t.charges_inr != null && (
              <span className="faint small"> ({inr(Math.round(t.charges_inr))} charges)</span>
            )}
            {!real && (
              <Badge
                tone="warning"
                title="Recorded before the ₹1L ledger — ₹ figures modelled at a fixed reference notional."
              >
                modelled
                <InfoTip term="modeled_row" />
              </Badge>
            )}
          </>
        );
      },
    },
    {
      id: "exit",
      header: "Exit reason",
      label: "Exit reason",
      cell: (t) => <span className="chip">{t.exit_reason}</span>,
    },
  ];

  return (
    <Section
      title={`Today's closed trades (${trades.length})`}
      note="Each row carries the plain-English reason the engine gave when it acted."
    >
      <DataTable
        label="Trades closed today"
        columns={columns}
        rows={sorted}
        rowKey={(t) => t.id}
        subRow={(t) => {
          const why = whyFor(t, preds);
          return why ? <span>Why: {why}</span> : null;
        }}
        empty={
          <EmptyState title="No closed trades yet today">
            They appear here as the live engine exits positions.
          </EmptyState>
        }
      />
    </Section>
  );
}

// Per-strategy contribution — makes "which logic made or lost the money" visible.
function PerStrategy({ rows }: { rows: PortfolioResponse["per_strategy"] }) {
  if (!rows || rows.length === 0) return null;
  const columns: Column<PortfolioResponse["per_strategy"][number]>[] = [
    { id: "strategy", header: "Strategy", primary: true, cell: (r) => r.strategy },
    { id: "trades", header: "Trades", label: "Trades", numeric: true, cell: (r) => r.trades },
    {
      id: "pnl",
      header: "Net P&L",
      label: "Net P&L",
      numeric: true,
      cell: (r) => <Money value={r.pnl_inr} kind="signed" />,
    },
    {
      id: "invested",
      header: "Invested now",
      label: "Invested now",
      numeric: true,
      cell: (r) => <Money value={r.invested_now} />,
    },
  ];
  return (
    <Section
      titleNode={
        <>
          By strategy
          <InfoTip term="by_strategy" />
        </>
      }
    >
      <DataTable
        label="Contribution by strategy"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.strategy}
      />
      <Callout tone="warning" icon="⚠">
        A record of what happened, not evidence that anything works. Net P&amp;L here is
        after modelled charges, and the sample is far too small to separate skill from
        noise.
      </Callout>
    </Section>
  );
}
