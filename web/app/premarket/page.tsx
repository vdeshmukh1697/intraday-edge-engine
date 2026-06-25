"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getPremarket,
  todayStr,
  type PremarketResponse,
} from "@/lib/api";
import { conf, signed } from "@/lib/format";
import { InfoTip } from "@/components/InfoTip";

export default function PremarketPage() {
  const [date, setDate] = useState<string>(todayStr());
  const [count, setCount] = useState<number>(40);
  const [data, setData] = useState<PremarketResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getPremarket(date, { top: count }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [date, count]);

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

      {/* How the picks are made — answers "why these stocks / how chosen". */}
      <div className="card explain" style={{ marginBottom: 16 }}>
        <strong>How pre-open picks are chosen.</strong> Before the bell we score the{" "}
        {data?.meta ? data.meta.scored : "most-liquid"} most-liquid NSE names for the most likely
        opening move. Each name gets a directional <em>bias</em> (LONG/SHORT) from four inputs —
        overnight <InfoTip term="adr" /> moves, the expected index gap, overnight{" "}
        <InfoTip term="catalyst" /> news, and the prior day&apos;s momentum — then they&apos;re
        ranked by conviction and the top {count} are shown. Use the count control to see more.
        {data?.meta && (
          <span className="muted small">
            {" "}Source: {data.meta.universe_source}; cues = {data.meta.cues ?? "—"}, news ={" "}
            {data.meta.news ?? "—"}.
          </span>
        )}
        <span className="muted small">
          {" "}Note: real ADR/news catalysts only exist for headline names, so many picks lean on
          index gap + momentum. Decision-support only — no orders.
        </span>
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
        <div className="field">
          <label>Show top</label>
          <input
            type="number"
            min={1}
            max={200}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
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
              <Metric label="Gap bias" term="gap_bias" value={data.outlook.gap_bias} />
              <Metric
                label="Expected gap"
                term="expected_gap"
                value={`${signed(data.outlook.expected_gap_pct)}%`}
              />
              <Metric label="Risk tone" term="risk_tone" value={data.outlook.risk_tone} />
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

          <h2>
            Pre-open picks{" "}
            {data.meta && (
              <span className="muted small">
                (showing {data.meta.shown} of {data.meta.scored} scored)
              </span>
            )}
          </h2>
          {data.picks.length === 0 ? (
            <div className="notice">No pre-open picks for the selected day.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Bias<InfoTip term="bias" /></th>
                    <th>Setup<InfoTip term="setup" /></th>
                    <th className="num">Exp gap<InfoTip term="expected_gap" /></th>
                    <th className="num">Conf<InfoTip term="confidence" /></th>
                    <th>Catalyst<InfoTip term="catalyst" /></th>
                    <th>Drivers<InfoTip term="drivers" /></th>
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

function Metric({ label, value, term }: { label: string; value: string; term?: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}{term && <InfoTip term={term} />}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}
