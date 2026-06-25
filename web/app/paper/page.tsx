"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { InfoTip } from "@/components/InfoTip";
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

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const pct = (n: number) => `${n.toFixed(2)}%`;
const cls = (n: number) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

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

  // Keep the latest filter values for the polling closure without re-arming the timer each keystroke.
  const filterRef = useRef({ start, end, symbol });
  filterRef.current = { start, end, symbol };

  const load = useCallback((spinner = true) => {
    if (spinner) setLoading(true);
    setError(null);
    const { start, end, symbol } = filterRef.current;
    const f = { start: start || undefined, end: end || undefined, symbol: symbol || undefined };
    Promise.all([getPaperAnalytics(f), getPaperTrades(f), getOpenPositions(), getLiveStatus()])
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

  useEffect(() => { load(); }, [load]); // initial load

  // Auto-refresh: poll every REFRESH_MS so new entries/exits show up live.
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [auto, load]);

  if (error) return <div className="card">Could not load paper trades: {error}</div>;
  if (!report) return <div className="card">Loading…</div>;

  const s = report.summary;
  const hasData = s.n_trades > 0;

  return (
    <div className="paper">
      <div className="page-head">
        <h1>Paper Trading</h1>
        <p className="muted">
          Recorded simulated trades & performance over time. Decision-support only — no live
          orders. Absolute P&amp;L assumes a fixed {inr(report.notional_per_trade)} notional per
          trade (the tool is capital-agnostic).
        </p>
      </div>

      <LiveBar status={status} lastRefresh={lastRefresh} auto={auto}
        onToggle={() => setAuto((a) => !a)} onRefresh={() => load(true)} loading={loading} />

      {/* Open positions — live entries currently in the market (the piece that was missing). */}
      <OpenPositions positions={open} notional={report.notional_per_trade} />

      <div className="filters card">
        <label>From <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>To <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Symbol <input placeholder="e.g. RELIANCE" value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></label>
        <button onClick={() => load(true)} disabled={loading}>{loading ? "…" : "Apply"}</button>
      </div>

      {!hasData ? (
        <div className="card empty">
          No paper trades recorded yet. They appear here automatically once the live paper-trader
          (or a <code>--persist</code> backtest) records trades.
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="cards">
            <Card label="Net P&L" term="net_pnl" value={inr(s.total_pnl_abs)} tone={cls(s.total_pnl_abs)} sub={pct(s.total_pnl_pct)} />
            <Card label="Win rate" term="win_rate" value={`${s.win_rate.toFixed(1)}%`} sub={`${s.wins}W / ${s.losses}L`} />
            <Card label="Trades" term="expectancy" value={String(s.n_trades)} sub={`exp ${inr(s.expectancy)}/trade`} />
            <Card label="Profit factor" term="profit_factor" value={s.profit_factor === null ? "∞" : s.profit_factor.toFixed(2)}
              tone={s.profit_factor !== null && s.profit_factor < 1 ? "neg" : "pos"} sub="gross win / gross loss" />
            <Card label="Avg win / loss" term="avg_win_loss" value={`${inr(s.avg_win)} / ${inr(s.avg_loss)}`} />
            <Card label="Max drawdown" term="max_drawdown" value={inr(s.max_drawdown)} tone="neg" sub="peak-to-trough" />
            <Card label="Best trade" term="best_worst" value={s.best_trade ? inr(s.best_trade.net_pnl_abs) : "—"} tone="pos" sub={s.best_trade?.symbol} />
            <Card label="Worst trade" term="best_worst" value={s.worst_trade ? inr(s.worst_trade.net_pnl_abs) : "—"} tone="neg" sub={s.worst_trade?.symbol} />
          </div>

          {/* Auto summary */}
          {report.auto_summary.length > 0 && (
            <div className="card auto">
              <h3>What the numbers say</h3>
              <ul>{report.auto_summary.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          )}

          {/* Equity curve + drawdown */}
          <div className="card">
            <h3>Equity curve (cumulative net P&L)<InfoTip term="equity_curve" /></h3>
            <LineSvg pts={report.equity_curve.map((p) => p.cum_pnl)} fmt={inr} />
            <h3 style={{ marginTop: 18 }}>Drawdown<InfoTip term="drawdown_series" /></h3>
            <AreaSvg pts={report.drawdown.map((p) => p.drawdown)} fmt={inr} negative />
          </div>

          {/* P&L distribution */}
          <div className="card">
            <h3>P&L distribution (per trade)<InfoTip term="pnl_distribution" /></h3>
            <Histogram bins={report.histogram} />
          </div>

          {/* By strategy / symbol */}
          <div className="grid2">
            <GroupTable title="By strategy" titleTerm="by_strategy" rows={report.by_strategy} keyName="strategy" />
            <GroupTable title="By symbol" titleTerm="by_symbol" rows={report.by_symbol} keyName="symbol" />
          </div>

          {/* By time of day */}
          <div className="card">
            <h3>By time of day<InfoTip term="by_tod" /></h3>
            <GroupBars rows={report.by_time_of_day} keyName="tod" />
          </div>

          {/* Trade history */}
          <div className="card">
            <h3>Trade history ({trades.length})</h3>
            <TradeTable trades={trades} />
          </div>
        </>
      )}
    </div>
  );
}

function Card({ label, value, sub, tone, term }: { label: string; value: string; sub?: string; tone?: string; term?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}{term && <InfoTip term={term} />}</div>
      <div className={`metric-value ${tone || ""}`}>{value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}

// Live-feed status strip: connection dot, last-update age, open/closed counts, auto-refresh toggle.
function LiveBar({ status, lastRefresh, auto, onToggle, onRefresh, loading }: {
  status: LiveStatus | null; lastRefresh: Date | null; auto: boolean;
  onToggle: () => void; onRefresh: () => void; loading: boolean;
}) {
  const live = status?.live && !status?.stale;
  const dot = live ? "live" : status?.live ? "stale" : "off";
  const label = live
    ? `Feed live — last bar ${status?.age_seconds != null ? `${status.age_seconds}s ago` : "just now"}`
    : status?.live
      ? `Feed stale — no update for ${status?.age_seconds ?? "?"}s (market closed or feed down)`
      : "Feed offline — start the live session to record trades";
  return (
    <div className="card livebar">
      <span className={`live-dot ${dot}`} />
      <span className="live-label">{label}</span>
      {status?.live && (
        <span className="live-counts">
          {status.open_count} open · {status.closed_today} closed today · {status.watching} watched
        </span>
      )}
      <span className="live-spacer" />
      {lastRefresh && (
        <span className="muted small">refreshed {lastRefresh.toLocaleTimeString("en-IN")}</span>
      )}
      <label className="toggle small">
        <input type="checkbox" checked={auto} onChange={onToggle} /> auto
      </label>
      <button className="ghost" onClick={onRefresh} disabled={loading}>
        {loading ? "…" : "Refresh"}
      </button>
    </div>
  );
}

// Live open positions table with entry/stop/target (₹ + %) and unrealized P&L.
function OpenPositions({ positions, notional }: { positions: OpenPosition[]; notional: number }) {
  if (positions.length === 0) {
    return (
      <div className="card empty small">
        No open positions right now. Live entries appear here the instant they fill (auto-refreshing).
      </div>
    );
  }
  const totUpnl = positions.reduce((a, p) => a + (p.unrealized_pnl_abs || 0), 0);
  return (
    <div className="card">
      <h3>
        Open positions ({positions.length}){" "}
        <span className={cls(totUpnl)}>· unrealized {inr(totUpnl)}</span>
      </h3>
      <table className="grid">
        <thead>
          <tr>
            <th>Symbol</th><th>Dir<InfoTip term="direction" /></th><th className="num">Entry ₹<InfoTip term="entry" /></th>
            <th className="num">LTP ₹<InfoTip term="ltp" /></th><th className="num">Target<InfoTip term="target" /></th>
            <th className="num">Stop<InfoTip term="stop" /></th><th className="num">Unrealized<InfoTip term="unrealized_pnl" /></th><th>Since</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.id} className="active-row">
              <td className="mono">
                <Link href={`/stock/${encodeURIComponent(p.symbol)}`}>{p.symbol}</Link>
              </td>
              <td><span className={`tag ${p.direction === "LONG" ? "pos" : "neg"}`}>{p.direction}</span></td>
              <td className="num">{p.entry?.toFixed(2)}</td>
              <td className="num">{p.last_price != null ? p.last_price.toFixed(2) : "—"}</td>
              <td className="num">
                {p.target != null ? p.target.toFixed(2) : "—"}
                {p.target_pct != null && <span className="muted small"> ({p.target_pct >= 0 ? "+" : ""}{p.target_pct.toFixed(2)}%)</span>}
              </td>
              <td className="num">
                {p.stop_loss != null ? p.stop_loss.toFixed(2) : "—"}
                {p.stop_pct != null && <span className="muted small"> (-{p.stop_pct.toFixed(2)}%)</span>}
              </td>
              <td className={`num ${cls(p.unrealized_pnl_pct || 0)}`}>
                {p.unrealized_pnl_pct != null ? `${p.unrealized_pnl_pct >= 0 ? "+" : ""}${p.unrealized_pnl_pct.toFixed(2)}%` : "—"}
                {p.unrealized_pnl_abs != null && <span className="small"> ({inr(p.unrealized_pnl_abs)})</span>}
              </td>
              <td className="muted small">{p.entry_ts ? p.entry_ts.slice(11, 16) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small" style={{ marginTop: 8 }}>
        Unrealized P&amp;L is gross at the {inr(notional)} reference notional, marked to the latest bar.
      </p>
    </div>
  );
}

// Minimal dependency-free SVG line (equity curve).
function LineSvg({ pts, fmt }: { pts: number[]; fmt: (n: number) => string }) {
  const W = 760, H = 180, P = 28;
  if (pts.length < 2) return <div className="muted">Not enough points.</div>;
  const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
  const x = (i: number) => P + (i / (pts.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg">
      <line x1={P} y1={zero} x2={W - P} y2={zero} className="axis" />
      <path d={d} className="line" />
      <text x={P} y={14} className="lbl">{fmt(max)}</text>
      <text x={P} y={H - 6} className="lbl">{fmt(min)}</text>
    </svg>
  );
}

function AreaSvg({ pts, fmt, negative }: { pts: number[]; fmt: (n: number) => string; negative?: boolean }) {
  const W = 760, H = 110, P = 24;
  if (pts.length < 2) return <div className="muted">—</div>;
  const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
  const x = (i: number) => P + (i / (pts.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const d = `M${x(0)} ${y(0)} ` + pts.map((v, i) => `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ") +
    ` L${x(pts.length - 1)} ${y(0)} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg">
      <path d={d} className={negative ? "area-neg" : "area"} />
      <text x={P} y={H - 4} className="lbl">{fmt(min)}</text>
    </svg>
  );
}

function Histogram({ bins }: { bins: { lo: number; hi: number; count: number }[] }) {
  const max = Math.max(1, ...bins.map((b) => b.count));
  return (
    <div className="hist">
      {bins.map((b, i) => (
        <div key={i} className="hist-col" title={`${inr(b.lo)}..${inr(b.hi)}: ${b.count}`}>
          <div className={`hist-bar ${b.hi <= 0 ? "neg" : "pos"}`} style={{ height: `${(b.count / max) * 100}%` }} />
          <div className="hist-x">{Math.round((b.lo + b.hi) / 2)}</div>
        </div>
      ))}
    </div>
  );
}

function GroupTable({ title, titleTerm, rows, keyName }: { title: string; titleTerm?: string; rows: GroupRow[]; keyName: "strategy" | "symbol" }) {
  return (
    <div className="card">
      <h3>{title}{titleTerm && <InfoTip term={titleTerm} />}</h3>
      <table className="tbl">
        <thead><tr><th>{keyName}</th><th>Trades</th><th>Win%<InfoTip term="win_rate" /></th><th>Net P&L</th><th>PF<InfoTip term="profit_factor" /></th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r[keyName]}</td><td>{r.n_trades}</td><td>{r.win_rate.toFixed(0)}%</td>
              <td className={cls(r.total_pnl_abs)}>{inr(r.total_pnl_abs)}</td>
              <td>{r.profit_factor === null ? "∞" : r.profit_factor.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupBars({ rows, keyName }: { rows: GroupRow[]; keyName: "tod" }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.total_pnl_abs)));
  return (
    <div className="tod">
      {rows.map((r, i) => (
        <div key={i} className="tod-row">
          <div className="tod-label">{r[keyName]}</div>
          <div className="tod-track">
            <div className={`tod-bar ${cls(r.total_pnl_abs)}`} style={{ width: `${(Math.abs(r.total_pnl_abs) / max) * 100}%` }} />
          </div>
          <div className={`tod-val ${cls(r.total_pnl_abs)}`}>{inr(r.total_pnl_abs)} · {r.n_trades}t · {r.win_rate.toFixed(0)}%</div>
        </div>
      ))}
    </div>
  );
}

function TradeTable({ trades }: { trades: PaperTrade[] }) {
  const [sortKey, setSortKey] = useState<keyof PaperTrade>("entry_ts");
  const [asc, setAsc] = useState(false);
  const sorted = [...trades].sort((a, b) => {
    const av = a[sortKey] as number | string, bv = b[sortKey] as number | string;
    return (av < bv ? -1 : av > bv ? 1 : 0) * (asc ? 1 : -1);
  });
  const head = (k: keyof PaperTrade, label: string) => (
    <th onClick={() => { setSortKey(k); setAsc(sortKey === k ? !asc : true); }} style={{ cursor: "pointer" }}>
      {label}{sortKey === k ? (asc ? " ▲" : " ▼") : ""}
    </th>
  );
  return (
    <div className="tbl-scroll">
      <table className="tbl">
        <thead><tr>
          {head("entry_ts", "Entry")}{head("symbol", "Symbol")}{head("direction", "Dir")}
          {head("strategy", "Strategy")}{head("confidence", "Conf")}{head("entry_fill", "Entry ₹")}
          {head("exit_fill", "Exit ₹")}{head("qty", "Qty")}{head("exit_reason", "Exit")}
          {head("costs_abs", "Costs")}{head("net_pnl_abs", "Net P&L")}{head("net_pnl_pct", "%")}
        </tr></thead>
        <tbody>
          {sorted.map((t) => (
            <tr key={t.id}>
              <td className="mono">{(t.entry_ts || "").replace("T", " ").slice(0, 16)}</td>
              <td>{t.symbol}</td>
              <td className={t.direction === "LONG" ? "pos" : "neg"}>{t.direction}</td>
              <td>{t.strategy}</td>
              <td>{t.confidence?.toFixed(0)}</td>
              <td className="mono">{t.entry_fill?.toFixed(2)}</td>
              <td className="mono">{t.exit_fill?.toFixed(2)}</td>
              <td className="mono">{t.qty}</td>
              <td>{t.exit_reason}</td>
              <td className="mono">{inr(t.costs_abs)}</td>
              <td className={`mono ${cls(t.net_pnl_abs)}`}>{inr(t.net_pnl_abs)}</td>
              <td className={cls(t.net_pnl_abs)}>{t.net_pnl_pct?.toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
