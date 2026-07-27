"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InfoTip } from "@/components/InfoTip";
import FeedStatus from "@/components/FeedStatus";
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
import {
  AreaChart,
  BarRows,
  Histogram,
  Hero,
  LineChart,
  StatTile,
} from "@/components/ui/stats";
import { DirectionTag, Money, SymbolLink } from "@/components/ui/cells";
import {
  getPaperAnalytics,
  getPaperTrades,
  getOpenPositions,
  getLiveStatus,
  type PaperReport,
  type PaperTrade,
  type GroupRow,
  type OpenPosition,
  type LiveStatus,
} from "@/lib/api";
import {
  conf,
  dayLabel,
  hhmm,
  inr,
  inrSigned,
  isoDay,
  num,
  pct,
  pctSigned,
  signArrow,
  signCls,
} from "@/lib/format";

const REFRESH_MS = 15000; // auto-refresh so trades appear without a manual reload

export default function PaperTradingPage() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [symbol, setSymbol] = useState("");
  const [report, setReport] = useState<PaperReport | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [open, setOpen] = useState<OpenPosition[]>([]);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [auto, setAuto] = useState(true);

  // Keep the latest filter values for the polling closure without re-arming the
  // timer on each keystroke.
  const filterRef = useRef({ start, end, symbol });
  filterRef.current = { start, end, symbol };

  const load = useCallback((spinner = true) => {
    if (spinner) setLoading(true);
    setError(null);
    const f = {
      start: filterRef.current.start || undefined,
      end: filterRef.current.end || undefined,
      symbol: filterRef.current.symbol || undefined,
    };
    Promise.all([
      getPaperAnalytics(f),
      getPaperTrades(f),
      getOpenPositions(),
      getLiveStatus(),
    ])
      .then(([r, t, o, st]) => {
        setReport(r);
        setTrades(t.trades);
        setOpen(o.positions);
        setStatus(st);
        setLastRefresh(new Date());
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [auto, load]);

  if (error && !report) {
    return (
      <>
        <PageHeader title="Paper trading account" />
        <ErrorBanner>Could not load paper trades: {error}</ErrorBanner>
      </>
    );
  }
  if (!report) {
    return (
      <>
        <PageHeader title="Paper trading account" />
        <LoadingBlock label="Loading paper-trading analytics" />
      </>
    );
  }

  const s = report.summary;
  const hasData = s.n_trades > 0;
  const cap = report.account_capital || 0;
  const unrealized = open.reduce((a, p) => a + (p.unrealized_pnl_abs || 0), 0);
  const total = s.total_pnl_abs + unrealized;
  const pctOf = (n: number) => (cap > 0 ? (n / cap) * 100 : 0);

  return (
    <>
      <PageHeader
        title="Paper trading account"
        eyebrow="Simulated broker statement"
        lede={
          <>
            A simulated broker account: {inr(report.account_capital)} capital,{" "}
            {inr(report.notional_per_trade)} deployed per trade, with a real cost model
            applied to every fill.{" "}
            <strong>Decision-support only — no live orders are ever placed.</strong>
          </>
        }
        aside={
          <Badge tone="warning" size="lg">
            Paper money
          </Badge>
        }
      />

      <FeedStatus
        status={status}
        lastRefresh={lastRefresh}
        auto={auto}
        onToggleAuto={() => setAuto((a) => !a)}
        onRefresh={() => load(true)}
        busy={loading}
      />

      {/* 1 — headline scorecard */}
      <Card>
        <div className="hero-split">
          <Hero
            label="Account value"
            value={inr(cap + total)}
            tone={signCls(total)}
          >
            <span className={signCls(total)}>
              <span className="delta-arrow" aria-hidden="true">
                {signArrow(total)}
              </span>{" "}
              {inrSigned(total)} ({pctSigned(pctOf(total))})
            </span>
            <span className="faint">on {inr(cap)} capital</span>
          </Hero>
          <div className="stat-grid">
            <StatTile
              label={
                <>
                  Realized P&amp;L
                  <InfoTip term="net_pnl" />
                </>
              }
              value={inrSigned(s.total_pnl_abs)}
              tone={signCls(s.total_pnl_abs)}
              sub={`closed · ${pctSigned(pctOf(s.total_pnl_abs))} of capital`}
            />
            <StatTile
              label={
                <>
                  Unrealized P&amp;L
                  <InfoTip term="unrealized_pnl" />
                </>
              }
              value={inrSigned(unrealized)}
              tone={signCls(unrealized)}
              sub={open.length ? `${open.length} open` : "no open positions"}
            />
            <StatTile
              label={
                <>
                  Win rate
                  <InfoTip term="win_rate" />
                </>
              }
              value={`${s.win_rate.toFixed(1)}%`}
              sub={`${s.wins}W / ${s.losses}L over ${s.n_trades} trades`}
            />
            <StatTile
              label={
                <>
                  Profit factor
                  <InfoTip term="profit_factor" />
                </>
              }
              value={s.profit_factor === null ? "∞" : s.profit_factor.toFixed(2)}
              tone={
                s.profit_factor !== null && s.profit_factor < 1 ? "neg" : undefined
              }
              sub="gross win ÷ gross loss · 1.00 is break-even"
            />
            <StatTile
              label={
                <>
                  Expectancy
                  <InfoTip term="expectancy" />
                </>
              }
              value={inrSigned(s.expectancy)}
              tone={signCls(s.expectancy)}
              sub="per trade, after costs"
            />
            <StatTile
              label={
                <>
                  Max drawdown
                  <InfoTip term="max_drawdown" />
                </>
              }
              value={inr(s.max_drawdown)}
              tone="neg"
              sub={`${pct(pctOf(s.max_drawdown))} of capital, peak to trough`}
            />
          </div>
        </div>
      </Card>

      <OpenPositions positions={open} notional={report.notional_per_trade} />

      <form
        className="control-bar"
        onSubmit={(e) => {
          e.preventDefault();
          load(true);
        }}
      >
        <label className="field">
          <span>From</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="field">
          <span>Symbol</span>
          <input
            placeholder="e.g. RELIANCE"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "…" : "Apply"}
        </button>
      </form>

      {!hasData ? (
        <EmptyState title="No paper trades recorded yet">
          They appear here automatically once the live paper-trader (or a{" "}
          <code>--persist</code> backtest) records trades.
        </EmptyState>
      ) : (
        <>
          {/* 2 — interpretation, deliberately styled as commentary, not data */}
          {report.auto_summary.length > 0 && (
            <Callout icon="🧭" title="What the numbers say">
              <ul>
                {report.auto_summary.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
              <p className="tiny faint" style={{ marginTop: "var(--space-2)" }}>
                Generated from the table above — an interpretation, not extra evidence.
                The sample is small; none of it demonstrates an edge.
              </p>
            </Callout>
          )}

          {/* 3 — equity and drawdown */}
          <Section
            titleNode={
              <>
                Equity &amp; drawdown
                <InfoTip term="equity_curve" />
              </>
            }
            note="Cumulative net P&L across the filtered trades, and how far below its own peak the curve sat at each point."
          >
            <div className="stack">
              <LineChart
                points={report.equity_curve.map((p) => p.cum_pnl)}
                labels={report.equity_curve.map((p) => `${p.symbol} ${hhmm(p.ts)}`)}
                fmt={inr}
                label="Cumulative net profit and loss"
              />
              <div>
                <h3 className="stat-label">
                  Drawdown
                  <InfoTip term="drawdown_series" />
                </h3>
                <AreaChart
                  points={report.drawdown.map((p) => p.drawdown)}
                  fmt={inr}
                  label="Drawdown from peak"
                />
              </div>
            </div>
          </Section>

          {/* 4 — distribution */}
          <Section
            titleNode={
              <>
                P&amp;L distribution
                <InfoTip term="pnl_distribution" />
              </>
            }
            note="Per-trade results by bucket (₹, mid-point labelled). Losses left of zero, gains right."
          >
            <Histogram bins={report.histogram} fmt={inr} />
          </Section>

          {/* 5 — breakdowns */}
          <div className="two-col">
            <GroupTable
              title="By strategy"
              titleTerm="by_strategy"
              rows={report.by_strategy}
              keyName="strategy"
            />
            <GroupTable
              title="By symbol"
              titleTerm="by_symbol"
              rows={report.by_symbol}
              keyName="symbol"
            />
          </div>

          <Section
            titleNode={
              <>
                By time of day
                <InfoTip term="by_tod" />
              </>
            }
            note="Net ₹ by entry time. With this few trades per bucket, differences here are almost certainly noise."
          >
            <BarRows
              rows={report.by_time_of_day.map((r) => ({
                key: r.tod ?? "",
                label: r.tod ?? "",
                value: r.total_pnl_abs,
                valueText: inrSigned(r.total_pnl_abs),
                meta: `${r.n_trades}t · ${r.win_rate.toFixed(0)}% win`,
              }))}
            />
          </Section>

          {/* 6 — the raw record */}
          <TradeHistory trades={trades} capital={cap} />
        </>
      )}
    </>
  );
}

// Live open positions table with entry/stop/target (₹ + %) and unrealized P&L.
function OpenPositions({
  positions,
  notional,
}: {
  positions: OpenPosition[];
  notional: number;
}) {
  const totUpnl = positions.reduce((a, p) => a + (p.unrealized_pnl_abs || 0), 0);
  const columns: Column<OpenPosition>[] = [
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
      id: "dir",
      header: (
        <>
          Dir
          <InfoTip term="direction" />
        </>
      ),
      label: "Direction",
      hideOnStack: true,
      cell: (p) => <DirectionTag direction={p.direction} />,
    },
    {
      id: "unreal",
      header: (
        <>
          Unrealized
          <InfoTip term="unrealized_pnl" />
        </>
      ),
      label: "Unrealized",
      numeric: true,
      cell: (p) => (
        <span className={signCls(p.unrealized_pnl_pct)}>
          {pctSigned(p.unrealized_pnl_pct)}
          {p.unrealized_pnl_abs != null && (
            <span className="small"> ({inrSigned(p.unrealized_pnl_abs)})</span>
          )}
        </span>
      ),
    },
    {
      id: "entry",
      header: (
        <>
          Entry ₹<InfoTip term="entry" />
        </>
      ),
      label: "Entry",
      numeric: true,
      cell: (p) => <Money value={p.entry} kind="price" />,
    },
    {
      id: "ltp",
      header: (
        <>
          LTP ₹<InfoTip term="ltp" />
        </>
      ),
      label: "Last price",
      numeric: true,
      cell: (p) => <Money value={p.last_price} kind="price" />,
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
      cell: (p) => (
        <>
          <Money value={p.target} kind="price" />
          {p.target_pct != null && (
            <span className="faint small"> ({pctSigned(p.target_pct)})</span>
          )}
        </>
      ),
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
      cell: (p) => (
        <>
          <Money value={p.stop_loss} kind="price" />
          {p.stop_pct != null && (
            <span className="faint small"> (−{p.stop_pct.toFixed(2)}%)</span>
          )}
        </>
      ),
    },
    {
      id: "since",
      header: "Since",
      label: "Open since",
      cell: (p) => <span className="mono faint">{hhmm(p.entry_ts)}</span>,
    },
  ];

  return (
    <Section
      title={`Open positions (${positions.length})`}
      aside={
        positions.length > 0 ? (
          <span className={signCls(totUpnl)}>
            unrealized <strong>{inrSigned(totUpnl)}</strong>
          </span>
        ) : undefined
      }
      note={`Unrealized P&L is gross at the ${inr(notional)} reference notional, marked to the latest bar.`}
    >
      <DataTable
        label="Open paper positions"
        columns={columns}
        rows={positions}
        rowKey={(p) => p.id}
        rowFlag={() => "active"}
        empty={
          <EmptyState title="No open positions right now">
            Live entries appear here the instant they fill.
          </EmptyState>
        }
      />
    </Section>
  );
}

function GroupTable({
  title,
  titleTerm,
  rows,
  keyName,
}: {
  title: string;
  titleTerm?: string;
  rows: GroupRow[];
  keyName: "strategy" | "symbol";
}) {
  const columns: Column<GroupRow>[] = [
    {
      id: keyName,
      header: keyName === "symbol" ? "Symbol" : "Strategy",
      primary: true,
      cell: (r) =>
        keyName === "symbol" && r.symbol ? (
          <>
            <SymbolLink symbol={r.symbol} />
            <span className={`stack-only ${signCls(r.total_pnl_abs)}`}>
              {inrSigned(r.total_pnl_abs)}
            </span>
          </>
        ) : (
          <>
            <span>{r.strategy}</span>
            <span className={`stack-only ${signCls(r.total_pnl_abs)}`}>
              {inrSigned(r.total_pnl_abs)}
            </span>
          </>
        ),
    },
    {
      id: "trades",
      header: "Trades",
      label: "Trades",
      numeric: true,
      cell: (r) => r.n_trades,
      sortBy: (r) => r.n_trades,
    },
    {
      id: "win",
      header: (
        <>
          Win %<InfoTip term="win_rate" />
        </>
      ),
      label: "Win rate",
      numeric: true,
      cell: (r) => `${r.win_rate.toFixed(0)}%`,
      sortBy: (r) => r.win_rate,
    },
    {
      id: "pnl",
      header: "Net P&L",
      label: "Net P&L",
      numeric: true,
      hideOnStack: true,
      cell: (r) => <Money value={r.total_pnl_abs} kind="signed" />,
      sortBy: (r) => r.total_pnl_abs,
    },
    {
      id: "pf",
      header: (
        <>
          PF
          <InfoTip term="profit_factor" />
        </>
      ),
      label: "Profit factor",
      numeric: true,
      cell: (r) => (r.profit_factor === null ? "∞" : r.profit_factor.toFixed(2)),
      sortBy: (r) => r.profit_factor ?? 0,
    },
  ];
  return (
    <Section
      titleNode={
        <>
          {title}
          {titleTerm && <InfoTip term={titleTerm} />}
        </>
      }
    >
      <DataTable
        label={title}
        columns={columns}
        rows={rows}
        rowKey={(r) => String(r[keyName] ?? "")}
        tall
      />
    </Section>
  );
}

// A trade's P&L is booked on the day it CLOSED, which is what the ledger's
// realized-per-day figure uses. Fall back to the entry day for a row missing an exit.
const tradeDay = (t: PaperTrade) => isoDay(t.exit_ts) || isoDay(t.entry_ts);

// Prefer the real ledger money (₹ written by the portfolio ledger); legacy rows fall
// back to the fixed-notional modelled figures, and say so.
const netOf = (t: PaperTrade) => t.pnl_inr ?? t.net_pnl_abs;
const costOf = (t: PaperTrade) => t.charges_inr ?? t.costs_abs;
const isModelled = (t: PaperTrade) => t.modeled ?? t.pnl_inr == null;

function dayTotals(rows: PaperTrade[]) {
  let net = 0;
  let charges = 0;
  let wins = 0;
  let losses = 0;
  let modelled = 0;
  for (const t of rows) {
    const n = netOf(t);
    net += n;
    charges += costOf(t);
    if (n > 0) wins += 1;
    else if (n < 0) losses += 1;
    if (isModelled(t)) modelled += 1;
  }
  // Derive gross rather than summing gross_pnl_abs: that column is always the
  // modelled figure, so adding it to ledger rows would mix two bases.
  return { net, charges, gross: net + charges, wins, losses, modelled, n: rows.length };
}

/** The raw trade record, filterable down to a single session. */
function TradeHistory({ trades, capital }: { trades: PaperTrade[]; capital: number }) {
  const [day, setDay] = useState("");  // "" = every day in the current range

  const days = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of trades) {
      const d = tradeDay(t);
      if (d) counts.set(d, (counts.get(d) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[0].localeCompare(a[0]));  // newest first
  }, [trades]);

  // The From/To filter above can drop the selected day out of the set entirely;
  // fall back to all days rather than showing an empty table with no explanation.
  useEffect(() => {
    if (day && !days.some(([d]) => d === day)) setDay("");
  }, [day, days]);

  const visible = useMemo(
    () => (day ? trades.filter((t) => tradeDay(t) === day) : trades),
    [trades, day]
  );
  const totals = useMemo(() => dayTotals(visible), [visible]);

  return (
    <Section
      title={`Trade history (${visible.length})`}
      note="Every recorded paper trade. Sort any column; rows marked “modelled” predate the ₹1L ledger and use a fixed reference notional."
      aside={
        days.length > 0 ? (
          <label className="field">
            <span>Day</span>
            <select value={day} onChange={(e) => setDay(e.target.value)}>
              <option value="">All days ({trades.length} trades)</option>
              {days.map(([d, count]) => (
                <option key={d} value={d}>
                  {dayLabel(d)} ({count} {count === 1 ? "trade" : "trades"})
                </option>
              ))}
            </select>
          </label>
        ) : undefined
      }
    >
      {day && (
        <div className="stack">
          <div className="stat-grid">
            <StatTile
              label={
                <>
                  Net result · {dayLabel(day)}
                  <InfoTip term="net_pnl" />
                </>
              }
              value={inrSigned(totals.net)}
              tone={signCls(totals.net)}
              sub={
                capital > 0
                  ? `${pctSigned((totals.net / capital) * 100)} of capital`
                  : "after all charges"
              }
            />
            <StatTile
              label="Trades closed"
              value={totals.n}
              sub={`${totals.wins}W / ${totals.losses}L`}
            />
            <StatTile
              label="Gross P&amp;L"
              value={inrSigned(totals.gross)}
              tone={signCls(totals.gross)}
              sub="before charges"
              emphasis="quiet"
            />
            <StatTile
              label="Charges"
              value={inr(totals.charges)}
              sub="brokerage, taxes, slippage"
              emphasis="quiet"
            />
          </div>
          {totals.modelled > 0 && (
            <p className="tiny faint">
              {totals.modelled} of {totals.n} rows on this day are modelled at a fixed
              reference notional (they predate the ₹1L ledger), so this total mixes two
              bases and is not a ledger figure.
            </p>
          )}
        </div>
      )}
      <TradeTable trades={visible} />
    </Section>
  );
}

function TradeTable({ trades }: { trades: PaperTrade[] }) {
  const columns: Column<PaperTrade>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (t) => (
        <>
          <SymbolLink symbol={t.symbol} />
          <span className={`stack-only mono ${signCls(netOf(t))}`}>
            {inrSigned(netOf(t))}
          </span>
        </>
      ),
    },
    {
      id: "entry_ts",
      header: "Entry",
      label: "Entry time",
      cell: (t) => (
        <span className="mono">{(t.entry_ts || "").replace("T", " ").slice(0, 16)}</span>
      ),
      sortBy: (t) => t.entry_ts,
    },
    {
      id: "dir",
      header: "Dir",
      label: "Direction",
      cell: (t) => <DirectionTag direction={t.direction} />,
      sortBy: (t) => t.direction,
    },
    {
      id: "strategy",
      header: "Strategy",
      label: "Strategy",
      hideOnStack: true,
      cell: (t) => t.strategy,
      sortBy: (t) => t.strategy,
    },
    {
      id: "rule",
      header: (
        <>
          Rule score
          <InfoTip term="confidence" />
        </>
      ),
      label: "Rule score (uncalibrated)",
      numeric: true,
      hideOnStack: true,
      cell: (t) => conf(t.confidence),
      sortBy: (t) => t.confidence,
    },
    {
      id: "entry_fill",
      header: "Entry ₹",
      label: "Entry price",
      numeric: true,
      cell: (t) => <Money value={t.entry_fill} kind="price" />,
      sortBy: (t) => t.entry_fill,
    },
    {
      id: "exit_fill",
      header: "Exit ₹",
      label: "Exit price",
      numeric: true,
      cell: (t) => <Money value={t.exit_fill} kind="price" />,
      sortBy: (t) => t.exit_fill,
    },
    {
      id: "qty",
      header: "Qty",
      label: "Qty",
      numeric: true,
      hideOnStack: true,
      cell: (t) => t.qty,
      sortBy: (t) => t.qty,
    },
    {
      id: "exit_reason",
      header: "Exit",
      label: "Exit reason",
      cell: (t) => <span className="chip">{t.exit_reason}</span>,
      sortBy: (t) => t.exit_reason,
    },
    {
      id: "costs",
      header: "Costs",
      label: "Charges",
      numeric: true,
      cell: (t) => <Money value={costOf(t)} />,
      sortBy: (t) => costOf(t),
    },
    {
      id: "net",
      header: "Net P&L",
      label: "Net P&L",
      numeric: true,
      hideOnStack: true,
      cell: (t) => (
        <>
          <Money value={netOf(t)} kind="signed" />
          {isModelled(t) && (
            <Badge
              tone="warning"
              title="Recorded before the ₹1L ledger — ₹ figures modelled at a fixed reference notional."
            >
              modelled
            </Badge>
          )}
        </>
      ),
      sortBy: (t) => netOf(t),
    },
    {
      id: "netpct",
      header: "%",
      label: "Net %",
      numeric: true,
      cell: (t) => (
        <span className={signCls(t.net_pnl_pct)}>{pctSigned(t.net_pnl_pct)}</span>
      ),
      sortBy: (t) => t.net_pnl_pct,
    },
    {
      id: "r",
      header: (
        <>
          R
          <InfoTip term="r_multiple" />
        </>
      ),
      label: "R-multiple",
      numeric: true,
      hideOnStack: true,
      cell: (t) => (t.r_multiple == null ? "—" : `${num(t.r_multiple)}R`),
      sortBy: (t) => t.r_multiple ?? 0,
    },
  ];

  return (
    <DataTable
      label="Paper trade history"
      columns={columns}
      rows={trades}
      rowKey={(t) => t.id}
      tall
      initialSort={{ id: "entry_ts", asc: false }}
      empty={<EmptyState title="No trades in this range" />}
    />
  );
}
