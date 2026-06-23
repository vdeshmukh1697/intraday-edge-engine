"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getBacktest,
  todayStr,
  type BacktestResponse,
} from "@/lib/api";
import { conf, num, pct, signed } from "@/lib/format";
import EquityChart from "@/components/EquityChart";
import HealthBadge from "@/components/HealthBadge";

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
    <div>
      <div className="page-head">
        <div>
          <h1>Backtest</h1>
          <div className="subtle">
            Walk-forward performance over the lookback window
          </div>
        </div>
        {data && <HealthBadge health={data.health} />}
      </div>

      <form
        className="controls"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <div className="field">
          <label>Start date</label>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Days</label>
          <input
            type="number"
            min={1}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Run"}
        </button>
      </form>

      {error && <div className="notice error">Failed to load: {error}</div>}

      {m && (
        <div className="card-grid">
          <Metric label="Trades" value={String(m.trades)} sub={`${data!.days.length} days`} />
          <Metric label="Win rate" value={conf(m.win_rate)} />
          <Metric
            label="Profit factor"
            value={m.profit_factor == null ? "—" : num(m.profit_factor)}
          />
          <Metric label="Expectancy" value={`${signed(m.expectancy_pct)}%`} />
          <Metric
            label="Total net"
            value={`${signed(m.total_net_pct)}%`}
            cls={m.total_net_pct >= 0 ? "pos" : "neg"}
          />
          <Metric
            label="Max drawdown"
            value={pct(m.max_drawdown_pct)}
            cls="neg"
          />
          <Metric label="Sharpe" value={num(m.sharpe)} />
          <Metric label="Sortino" value={num(m.sortino)} />
          <Metric
            label="Avg hold"
            value={`${Math.round(m.avg_hold_minutes)}m`}
          />
        </div>
      )}

      {data && data.equity_curve.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <h2>Equity curve</h2>
          <EquityChart
            equityCurve={data.equity_curve}
            dailyReturns={data.daily_returns}
          />
        </div>
      )}

      {data && (
        <div className="card">
          <h2>Strategy health</h2>
          <div className="card-grid" style={{ marginBottom: 6 }}>
            <Metric
              label="Overall"
              value={
                data.health.overall > 1
                  ? String(Math.round(data.health.overall))
                  : `${Math.round(data.health.overall * 100)}`
              }
              sub={data.health.status}
            />
            <Metric label="Hit rate" value={conf(data.health.hit_rate)} />
            <Metric
              label="Profit factor"
              value={
                data.health.profit_factor == null
                  ? "—"
                  : num(data.health.profit_factor)
              }
            />
            <Metric
              label="Expectancy"
              value={`${signed(data.health.expectancy_pct)}%`}
            />
            <Metric
              label="Calibration err"
              value={num(data.health.calibration_error, 3)}
            />
            <Metric
              label="Max drawdown"
              value={pct(data.health.max_drawdown_pct)}
              cls="neg"
            />
            <Metric
              label="Window trades"
              value={String(data.health.window_trades)}
            />
          </div>

          {Object.keys(data.health.components).length > 0 && (
            <>
              <h2 style={{ marginTop: 16 }}>Component breakdown</h2>
              <div className="health-components">
                {Object.entries(data.health.components).map(([name, v]) => {
                  const ratio = v > 1 ? v / 100 : v;
                  const widthPct = Math.max(0, Math.min(100, ratio * 100));
                  return (
                    <div className="comp" key={name}>
                      <div className="comp-name">{name.replace(/_/g, " ")}</div>
                      <div className="metric-value" style={{ fontSize: 18 }}>
                        {v > 1 ? Math.round(v) : num(v, 2)}
                      </div>
                      <div className="bar">
                        <span style={{ width: `${widthPct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  cls,
}: {
  label: string;
  value: string;
  sub?: string;
  cls?: string;
}) {
  return (
    <div className="metric card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value${cls ? ` ${cls}` : ""}`}>{value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  );
}
