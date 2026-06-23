"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getPremarket,
  todayStr,
  type PremarketResponse,
} from "@/lib/api";
import { conf, signed } from "@/lib/format";

export default function PremarketPage() {
  const [date, setDate] = useState<string>(todayStr());
  const [data, setData] = useState<PremarketResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getPremarket(date));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Pre-market</h1>
          <div className="subtle">
            Index outlook and pre-open picks{data ? ` · ${data.day}` : ""}
          </div>
        </div>
      </div>

      <form
        className="controls"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <div className="field">
          <label>Date</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </form>

      {error && <div className="notice error">Failed to load: {error}</div>}

      {data && (
        <>
          <div className="card" style={{ marginBottom: 20 }}>
            <h2>Index outlook</h2>
            <div className="card-grid" style={{ marginBottom: 12 }}>
              <Metric label="Gap bias" value={data.outlook.gap_bias} />
              <Metric
                label="Expected gap"
                value={`${signed(data.outlook.expected_gap_pct)}%`}
              />
              <Metric label="Risk tone" value={data.outlook.risk_tone} />
            </div>
            {data.outlook.drivers.length > 0 && (
              <div className="reasons">
                {data.outlook.drivers.map((d, i) => (
                  <span className="reason-chip" key={i}>
                    {d}
                  </span>
                ))}
              </div>
            )}
          </div>

          <h2>Pre-open picks</h2>
          {data.picks.length === 0 ? (
            <div className="notice">No pre-open picks for the selected day.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Bias</th>
                    <th>Setup</th>
                    <th className="num">Exp gap</th>
                    <th className="num">Conf</th>
                    <th>Catalyst</th>
                    <th>Drivers</th>
                  </tr>
                </thead>
                <tbody>
                  {data.picks.map((p, idx) => (
                    <tr key={`${p.symbol}-${idx}`}>
                      <td className="sym">
                        <Link
                          href={`/stock/${encodeURIComponent(
                            p.symbol
                          )}?date=${date}`}
                        >
                          {p.symbol}
                        </Link>
                      </td>
                      <td>
                        <span
                          className={`dir ${
                            /short|bear|down/i.test(p.bias) ? "short" : "long"
                          }`}
                        >
                          {p.bias}
                        </span>
                      </td>
                      <td>{p.setup}</td>
                      <td className="num">{signed(p.expected_gap_pct)}%</td>
                      <td className="num">{conf(p.confidence)}</td>
                      <td>{p.catalyst}</td>
                      <td>
                        <div className="reasons">
                          {p.drivers.map((d, i) => (
                            <span className="reason-chip" key={i}>
                              {d}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}
