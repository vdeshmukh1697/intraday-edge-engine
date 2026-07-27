"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getLeaderboard,
  getBacktest,
  todayStr,
  type LeaderboardEntry,
  type LeaderboardResponse,
  type StrategyHealth,
} from "@/lib/api";
import { conf, int, num, pct } from "@/lib/format";
import HealthBadge from "@/components/HealthBadge";
import { InfoTip } from "@/components/InfoTip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Callout,
  ChipRow,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { StatTile } from "@/components/ui/stats";
import { DirectionTag, SymbolLink } from "@/components/ui/cells";

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
      setData(await getLeaderboard({ date, universe, top, news, ml }));
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
      .then((b) => active && setHealth(b.health))
      .catch(() => active && setHealth(null));
    return () => {
      active = false;
    };
  }, [date]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stats = data?.stats;

  const columns: Column<LeaderboardEntry>[] = [
    {
      id: "rank",
      header: "#",
      label: "Rank",
      numeric: true,
      hideOnStack: true,
      cell: (e) => e.rank,
    },
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (e) => (
        <>
          <span>
            <span className="stack-only faint tiny">#{e.rank} </span>
            <SymbolLink symbol={e.symbol} date={date} />
          </span>
          <span className="stack-only">
            <DirectionTag direction={e.direction} />
          </span>
        </>
      ),
    },
    {
      id: "direction",
      header: (
        <>
          Dir
          <InfoTip term="direction" />
        </>
      ),
      label: "Direction",
      hideOnStack: true,
      cell: (e) => <DirectionTag direction={e.direction} />,
    },
    {
      id: "score",
      header: (
        <>
          Score
          <InfoTip term="score" />
        </>
      ),
      label: "Rank score",
      numeric: true,
      cell: (e) => num(e.score),
      sortBy: (e) => e.score,
    },
    {
      id: "entry",
      header: (
        <>
          Entry
          <InfoTip term="entry" />
        </>
      ),
      label: "Entry",
      numeric: true,
      cell: (e) => num(e.entry),
    },
    {
      id: "stop",
      header: (
        <>
          Stop %<InfoTip term="stop" />
        </>
      ),
      label: "Stop %",
      numeric: true,
      cell: (e) => pct(e.stop_pct),
    },
    {
      id: "t1",
      header: (
        <>
          T1 %<InfoTip term="target" />
        </>
      ),
      label: "Target %",
      numeric: true,
      cell: (e) => pct(e.t1_pct),
    },
    {
      id: "rr",
      header: (
        <>
          R:R
          <InfoTip term="rr" />
        </>
      ),
      label: "Risk : reward",
      numeric: true,
      cell: (e) => num(e.risk_reward),
      sortBy: (e) => e.risk_reward,
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
      cell: (e) => conf(e.confidence),
      sortBy: (e) => e.confidence,
    },
    ...(ml
      ? [
          {
            id: "ml",
            header: (
              <>
                ML conf
                <InfoTip term="ml_conf" />
              </>
            ),
            label: "ML shadow score",
            numeric: true,
            cell: (e: LeaderboardEntry) => conf(e.ml_confidence),
          } satisfies Column<LeaderboardEntry>,
        ]
      : []),
    {
      id: "move",
      header: (
        <>
          Exp move
          <InfoTip term="expected_move" />
        </>
      ),
      label: "Expected move",
      numeric: true,
      hideOnStack: true,
      cell: (e) => pct(e.expected_move_pct),
    },
    {
      id: "breakeven",
      header: (
        <>
          Break-even
          <InfoTip term="cost_to_break_even" />
        </>
      ),
      label: "Cost to break even",
      numeric: true,
      cell: (e) => pct(e.cost_to_break_even_pct),
      sortBy: (e) => e.cost_to_break_even_pct,
    },
    {
      id: "sector",
      header: (
        <>
          Sector
          <InfoTip term="sector" />
        </>
      ),
      label: "Sector",
      hideOnStack: true,
      cell: (e) => e.sector,
    },
    {
      id: "turnover",
      header: (
        <>
          Turnover (Cr)
          <InfoTip term="turnover" />
        </>
      ),
      label: "Turnover (₹cr)",
      numeric: true,
      hideOnStack: true,
      cell: (e) => num(e.turnover_cr, 1),
      sortBy: (e) => e.turnover_cr,
    },
    {
      id: "reasons",
      header: "Reasons",
      label: "Reasons",
      spanOnStack: true,
      cell: (e) => <ChipRow items={e.reasons} max={3} />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Leaderboard"
        eyebrow={data ? `Session ${data.day}` : "Ranked intraday signals"}
        lede={
          <>
            Everything the scan surfaced for the selected session, ranked. These are
            candidates the engine <em>may</em> act on during the day — not
            recommendations, and never orders you should place yourself.
          </>
        }
        aside={health ? <HealthBadge health={health} /> : undefined}
      />

      <Callout icon="ℹ" title="What these numbers are, and are not">
        <strong>Rule score</strong> counts how many rules aligned. It is{" "}
        <strong>uncalibrated</strong>: across the first 136 live paper trades it did not
        predict outcomes, so it is descriptive only — never a probability of profit, never
        a win rate. <strong>Break-even</strong> is the move needed just to pay round-trip
        costs; a setup whose expected move barely clears it has no room left.
      </Callout>

      <form
        className="control-bar"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <label className="field">
          <span>Date</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label className="field">
          <span>Universe</span>
          <input
            type="number"
            min={1}
            value={universe}
            onChange={(e) => setUniverse(Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>Top</span>
          <input
            type="number"
            min={1}
            value={top}
            onChange={(e) => setTop(Number(e.target.value))}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={news}
            onChange={(e) => setNews(e.target.checked)}
          />
          News veto
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={ml} onChange={(e) => setMl(e.target.checked)} />
          ML shadow
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </form>

      {error && <ErrorBanner>Failed to load: {error}</ErrorBanner>}

      {stats && (
        <Section
          title="Scan funnel"
          level={2}
          note="How many names survived each filter on the way to a tradeable candidate."
        >
          <div className="stat-grid">
            <Stat label="Universe" value={int(stats.universe)} term="universe" />
            <Stat label="Deep scanned" value={int(stats.deep_scanned)} term="deep_scanned" />
            <Stat label="Filtered out" value={int(stats.filtered_out)} term="filtered_out" />
            <Stat label="No signal" value={int(stats.no_signal)} term="no_signal" />
            <Stat label="Risk vetoed" value={int(stats.vetoed)} term="vetoed" />
            <Stat label="News vetoed" value={int(stats.news_vetoed)} term="news_vetoed" />
            <Stat label="Candidates" value={int(stats.candidates)} term="candidates" />
          </div>
        </Section>
      )}

      {loading && !data && <LoadingBlock label="Running the scan" />}

      {data && (
        <Section
          title={`Ranked candidates (${data.entries.length})`}
          note={
            ml
              ? "ML conf is a shadow score — it does not change the ranking."
              : "Ranked by composite score. Rank order is not a prediction of which will work."
          }
        >
          <DataTable
            label="Ranked intraday candidates"
            columns={columns}
            rows={data.entries}
            rowKey={(e) => `${e.rank}-${e.symbol}`}
            mobile="stack"
            empty={
              <EmptyState title="No candidates for this session">
                Every name was filtered, vetoed, or produced no signal. A quiet day is a
                normal outcome for a selective strategy.
              </EmptyState>
            }
          />
        </Section>
      )}
    </>
  );
}

function Stat({ label, value, term }: { label: string; value: string; term?: string }) {
  return (
    <StatTile
      label={
        <>
          {label}
          {term && <InfoTip term={term} />}
        </>
      }
      value={value}
      emphasis="quiet"
    />
  );
}
