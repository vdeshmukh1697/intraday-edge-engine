"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getPremarket,
  todayStr,
  type PremarketPick,
  type PremarketResponse,
} from "@/lib/api";
import { conf, signed } from "@/lib/format";
import { InfoTip } from "@/components/InfoTip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Badge,
  Callout,
  ChipRow,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { StatTile } from "@/components/ui/stats";
import { SymbolLink } from "@/components/ui/cells";

function biasTone(bias: string): "positive" | "negative" {
  return /short|bear|down/i.test(bias) ? "negative" : "positive";
}

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

  const columns: Column<PremarketPick>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (p) => (
        <>
          <SymbolLink symbol={p.symbol} date={date} />
          <span className="stack-only">
            <Badge tone={biasTone(p.bias)}>
              {biasTone(p.bias) === "positive" ? "▲" : "▼"} {p.bias}
            </Badge>
          </span>
        </>
      ),
    },
    {
      id: "bias",
      header: (
        <>
          Bias
          <InfoTip term="bias" />
        </>
      ),
      label: "Bias",
      hideOnStack: true,
      cell: (p) => (
        <Badge tone={biasTone(p.bias)}>
          {biasTone(p.bias) === "positive" ? "▲" : "▼"} {p.bias}
        </Badge>
      ),
    },
    {
      id: "setup",
      header: (
        <>
          Setup
          <InfoTip term="setup" />
        </>
      ),
      label: "Setup",
      cell: (p) => p.setup,
    },
    {
      id: "gap",
      header: (
        <>
          Exp gap
          <InfoTip term="expected_gap" />
        </>
      ),
      label: "Expected gap",
      numeric: true,
      cell: (p) => `${signed(p.expected_gap_pct)}%`,
      sortBy: (p) => p.expected_gap_pct,
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
      cell: (p) => conf(p.confidence),
      sortBy: (p) => p.confidence,
    },
    {
      id: "catalyst",
      header: (
        <>
          Catalyst
          <InfoTip term="catalyst" />
        </>
      ),
      label: "Catalyst",
      cell: (p) => p.catalyst,
    },
    {
      id: "drivers",
      header: (
        <>
          Drivers
          <InfoTip term="drivers" />
        </>
      ),
      label: "Drivers",
      spanOnStack: true,
      cell: (p) => <ChipRow items={p.drivers} />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Pre-market"
        eyebrow={data ? `Briefing for ${data.day}` : "08:30 briefing"}
        lede={
          <>
            Index outlook from overnight global cues, plus the pre-open watchlist ranked
            by conviction. Read before the bell to know what the day might look like —{" "}
            <strong>not a list of trades to place.</strong>
          </>
        }
      />

      <Callout icon="ℹ" title="How pre-open picks are chosen">
        Before the bell the engine scores the{" "}
        {data?.meta ? `${data.meta.scored} ` : ""}most-liquid NSE names for the most likely
        opening move. Each name gets a directional <em>bias</em> from four inputs —
        overnight ADR
        <InfoTip term="adr" /> moves, the expected index gap, overnight catalyst
        <InfoTip term="catalyst" /> news, and the prior day&apos;s momentum — then they are
        ranked and the top {count} shown.{" "}
        <strong>
          Real ADR/news catalysts only exist for headline names, so most picks lean on
          index gap plus momentum alone.
        </strong>{" "}
        Rule score is uncalibrated and is not a probability.
        {data?.meta && (
          <span className="faint small">
            {" "}
            Source: {data.meta.universe_source}; cues = {data.meta.cues ?? "—"}, news ={" "}
            {data.meta.news ?? "—"}.
          </span>
        )}
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
          <span>Show top</span>
          <input
            type="number"
            min={1}
            max={200}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
          />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </form>

      {error && <ErrorBanner>Failed to load: {error}</ErrorBanner>}
      {loading && !data && <LoadingBlock label="Loading the pre-market briefing" />}

      {data && (
        <>
          <Section
            title="Index outlook"
            note="Derived from overnight global cues (GIFT Nifty, US close, ADRs). A view on the open, not on the day."
          >
            <div className="stack">
              <div className="stat-grid">
                <StatTile
                  label={
                    <>
                      Gap bias
                      <InfoTip term="gap_bias" />
                    </>
                  }
                  value={data.outlook.gap_bias}
                />
                <StatTile
                  label={
                    <>
                      Expected gap
                      <InfoTip term="expected_gap" />
                    </>
                  }
                  value={`${signed(data.outlook.expected_gap_pct)}%`}
                />
                <StatTile
                  label={
                    <>
                      Risk tone
                      <InfoTip term="risk_tone" />
                    </>
                  }
                  value={data.outlook.risk_tone}
                />
              </div>
              {data.outlook.drivers.length > 0 && (
                <ChipRow items={data.outlook.drivers} />
              )}
            </div>
          </Section>

          <Section
            title={`Pre-open picks (${data.picks.length})`}
            note={
              data.meta
                ? `Showing ${data.meta.shown} of ${data.meta.scored} scored names.`
                : undefined
            }
          >
            <DataTable
              label="Pre-open ranked picks"
              columns={columns}
              rows={data.picks}
              rowKey={(p) => p.symbol}
              tall
              empty={
                <EmptyState title="No pre-open picks for this day">
                  The 08:30 job may not have run, or nothing scored above the threshold.
                </EmptyState>
              }
            />
          </Section>
        </>
      )}
    </>
  );
}
