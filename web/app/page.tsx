"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getLeaderboard,
  getBacktest,
  todayStr,
  type LeaderboardResponse,
  type StrategyHealth,
} from "@/lib/api";
import { conf, num, int, pct } from "@/lib/format";
import HealthBadge from "@/components/HealthBadge";
import { InfoTip } from "@/components/InfoTip";

export default function LeaderboardPage() {
  const [date, setDate] = useState<string>(todayStr());
  const [universe, setUniverse] = useState<number>(500);
  const [top, setTop] = useState<number>(20);
  const [news, setNews] = useState<boolean>(true);
  const [ml, setMl] = useState<boolean>(false);

  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [health, setHealth] = useState<StrategyHealth | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const lb = await getLeaderboard({ date, universe, top, news, ml });
      setData(lb);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [date, universe, top, news, ml]);

  // Strategy health is independent of leaderboard filters.
  useEffect(() => {
    let active = true;
    getBacktest({ start: date, days: 10 })
      .then((b) => {
        if (active) setHealth(b.health);
      })
      .catch(() => {
        if (active) setHealth(null);
      });
    return () => {
      active = false;
    };
  }, [date]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stats = data?.stats;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Leaderboard</h1>
          <div className="subtle">
            Ranked intraday signals{data ? ` · ${data.day}` : ""}
          </div>
        </div>
        {health && <HealthBadge health={health} />}
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
          <label>Universe</label>
          <input
            type="number"
            min={1}
            value={universe}
            onChange={(e) => setUniverse(Number(e.target.value))}
          />
        </div>
        <div className="field">
          <label>Top</label>
          <input
            type="number"
            min={1}
            value={top}
            onChange={(e) => setTop(Number(e.target.value))}
          />
        </div>
        <label className="toggle">
          <input
            type="checkbox"
            checked={news}
            onChange={(e) => setNews(e.target.checked)}
          />
          News veto
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={ml}
            onChange={(e) => setMl(e.target.checked)}
          />
          ML shadow
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </form>

      {error && <div className="notice error">Failed to load: {error}</div>}

      {stats && (
        <div className="stats-strip">
          <Stat label="Universe" value={int(stats.universe)} term="universe" />
          <Stat label="Deep scanned" value={int(stats.deep_scanned)} term="deep_scanned" />
          <Stat label="Filtered out" value={int(stats.filtered_out)} term="filtered_out" />
          <Stat label="No signal" value={int(stats.no_signal)} term="no_signal" />
          <Stat label="Vetoed" value={int(stats.vetoed)} term="vetoed" />
          <Stat label="News vetoed" value={int(stats.news_vetoed)} term="news_vetoed" />
          <Stat label="Candidates" value={int(stats.candidates)} term="candidates" />
        </div>
      )}

      {data && data.entries.length === 0 && !loading && (
        <div className="notice">No candidates for the selected day.</div>
      )}

      {data && data.entries.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Symbol</th>
                  <th>Dir <InfoTip term="direction" /></th>
                  <th className="num">Score <InfoTip term="score" /></th>
                  <th className="num">Entry <InfoTip term="entry" /></th>
                  <th className="num">Stop % <InfoTip term="stop" /></th>
                  <th className="num">T1 % <InfoTip term="target" /></th>
                  <th className="num">R:R <InfoTip term="rr" /></th>
                  <th className="num">Conf <InfoTip term="confidence" /></th>
                  {ml && <th className="num">ML conf <InfoTip term="ml_conf" /></th>}
                  <th className="num">Exp move <InfoTip term="expected_move" /></th>
                  <th className="num">Break-even <InfoTip term="cost_to_break_even" /></th>
                  <th>Sector <InfoTip term="sector" /></th>
                  <th className="num">Turnover (Cr) <InfoTip term="turnover" /></th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map((e) => (
                  <tr key={`${e.rank}-${e.symbol}`}>
                    <td className="num">{e.rank}</td>
                    <td className="sym">
                      <Link href={`/stock/${encodeURIComponent(e.symbol)}?date=${date}`}>
                        {e.symbol}
                      </Link>
                    </td>
                    <td>
                      <span
                        className={`dir ${
                          e.direction === "LONG" ? "long" : "short"
                        }`}
                      >
                        {e.direction}
                      </span>
                    </td>
                    <td className="num">{num(e.score)}</td>
                    <td className="num">{num(e.entry)}</td>
                    <td className="num">{pct(e.stop_pct)}</td>
                    <td className="num pos">{pct(e.t1_pct)}</td>
                    <td className="num">{num(e.risk_reward)}</td>
                    <td className="num">{conf(e.confidence)}</td>
                    {ml && (
                      <td className="num">{conf(e.ml_confidence)}</td>
                    )}
                    <td className="num">{pct(e.expected_move_pct)}</td>
                    <td className="num">{pct(e.cost_to_break_even_pct)}</td>
                    <td>{e.sector}</td>
                    <td className="num">{num(e.turnover_cr, 1)}</td>
                    <td>
                      <div className="reasons">
                        {e.reasons.map((r, i) => (
                          <span className="reason-chip" key={i}>
                            {r}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ml && (
            <p className="caption">
              conf = rules; ML conf = shadow model, does not change ranking.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  term,
}: {
  label: string;
  value: string;
  term?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-num">{value}</span>
      <span className="stat-lbl">
        {label}
        {term && <InfoTip term={term} />}
      </span>
    </div>
  );
}
