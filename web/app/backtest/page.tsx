"use client";

import { useCallback, useEffect, useState } from "react";
import { getBacktest, todayStr, type BacktestResponse } from "@/lib/api";
import { conf, num, pct, signed, signCls } from "@/lib/format";
import EquityChart from "@/components/EquityChart";
import HealthBadge from "@/components/HealthBadge";
import { InfoTip } from "@/components/InfoTip";
import {
  Callout,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { Meter, StatTile } from "@/components/ui/stats";

export default function BacktestPage() {
  const [start, setStart] = useState<string>(todayStr());
  const [days, setDays] = useState<number>(10);
  const [data, setData] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getBacktest({ start, days }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [start, days]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const m = data?.metrics;

  return (
    <>
      <PageHeader
        title="Backtest"
        eyebrow="Walk-forward over the lookback window"
        lede={
          <>
            How the same rules would have behaved over recent sessions. A backtest is the
            most flattering view a strategy ever gets:{" "}
            <strong>
              it knows which names survived, pays idealised fills, and has been looked at
              many times.
            </strong>{" "}
            Treat every figure here as an upper bound, not an expectation.
          </>
        }
        aside={data ? <HealthBadge health={data.health} /> : undefined}
      />

      <form
        className="control-bar"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <label className="field">
          <span>Start date</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span>Days</span>
          {/* max matches the API ceiling: each session-day is ~62 s of one core, so the box
              cannot silently buy a multi-hour backtest on a typo. */}
          <input
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Run"}
        </button>
      </form>

      {error && <ErrorBanner>Failed to load: {error}</ErrorBanner>}
      {loading && !data && <LoadingBlock label="Running the backtest" />}

      {m && data && (
        <Section
          title="Result"
          note={`${m.trades} trades across ${data.days.length} sessions. With a sample this size, none of these numbers separate skill from noise.`}
        >
          <div className="stat-grid">
            <StatTile label="Trades" value={String(m.trades)} sub={`${data.days.length} days`} />
            <StatTile label="Win rate" value={conf(m.win_rate) + "%"} />
            <StatTile
              label={
                <>
                  Profit factor
                  <InfoTip term="profit_factor" />
                </>
              }
              value={m.profit_factor == null ? "—" : num(m.profit_factor)}
              tone={m.profit_factor != null && m.profit_factor < 1 ? "neg" : undefined}
            />
            <StatTile
              label={
                <>
                  Expectancy
                  <InfoTip term="expectancy" />
                </>
              }
              value={`${signed(m.expectancy_pct)}%`}
              tone={signCls(m.expectancy_pct)}
              sub="per trade"
            />
            <StatTile
              label="Total net"
              value={`${signed(m.total_net_pct)}%`}
              tone={signCls(m.total_net_pct)}
            />
            <StatTile
              label={
                <>
                  Max drawdown
                  <InfoTip term="max_drawdown" />
                </>
              }
              value={pct(m.max_drawdown_pct)}
              tone="neg"
            />
            <StatTile
              label={
                <>
                  Sharpe
                  <InfoTip term="sharpe" />
                </>
              }
              value={num(m.sharpe)}
            />
            <StatTile
              label={
                <>
                  Sortino
                  <InfoTip term="sortino" />
                </>
              }
              value={num(m.sortino)}
            />
            <StatTile
              label={
                <>
                  Avg hold
                  <InfoTip term="avg_hold" />
                </>
              }
              value={`${Math.round(m.avg_hold_minutes)}m`}
            />
          </div>
        </Section>
      )}

      {data && (
        <Section
          title="Equity curve"
          note="Cumulative simulated return over the window. Idealised fills; no live capital was at risk."
        >
          {data.equity_curve.length > 0 ? (
            <EquityChart
              equityCurve={data.equity_curve}
              dailyReturns={data.daily_returns}
              label={`Backtest equity curve over ${data.days.length} sessions`}
            />
          ) : (
            <EmptyState title="No equity curve for this window" />
          )}
        </Section>
      )}

      {data && (
        <Section
          titleNode={
            <>
              Strategy health
              <InfoTip term="health_score" />
            </>
          }
          note="A composite gauge, descriptive only — it summarises what already happened and predicts nothing."
        >
          <div className="stack">
            <div className="stat-grid">
              <StatTile
                label="Overall"
                value={
                  data.health.overall > 1
                    ? String(Math.round(data.health.overall))
                    : String(Math.round(data.health.overall * 100))
                }
                sub={data.health.status}
              />
              <StatTile label="Hit rate" value={conf(data.health.hit_rate) + "%"} />
              <StatTile
                label="Profit factor"
                value={
                  data.health.profit_factor == null
                    ? "—"
                    : num(data.health.profit_factor)
                }
              />
              <StatTile
                label="Expectancy"
                value={`${signed(data.health.expectancy_pct)}%`}
                tone={signCls(data.health.expectancy_pct)}
              />
              <StatTile
                label={
                  <>
                    Calibration err
                    <InfoTip term="calibration" />
                  </>
                }
                value={num(data.health.calibration_error, 3)}
              />
              <StatTile
                label="Max drawdown"
                value={pct(data.health.max_drawdown_pct)}
                tone="neg"
              />
              <StatTile label="Window trades" value={String(data.health.window_trades)} />
            </div>

            {Object.keys(data.health.components).length > 0 && (
              <div>
                <h3 className="stat-label">Component breakdown</h3>
                <div className="meter-grid">
                  {Object.entries(data.health.components).map(([name, v]) => (
                    <Meter
                      key={name}
                      name={name.replace(/_/g, " ")}
                      value={v > 1 ? String(Math.round(v)) : num(v, 2)}
                      ratio={v > 1 ? v / 100 : v}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </Section>
      )}

      <Callout tone="warning" icon="⚠">
        Backtest results are not evidence of an edge. The live paper record on{" "}
        <a href="/paper">the paper-trading page</a> is the honest comparison — and it is
        the one that pays real charges.
      </Callout>
    </>
  );
}
