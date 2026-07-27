"use client";

import { useEffect, useState } from "react";
import { getMovers, type MoversResponse, type MoverPrediction } from "@/lib/api";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Badge,
  Callout,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { StatTile, TailSplitBar } from "@/components/ui/stats";
import { num, probPct, signCls } from "@/lib/format";

const POLL_MS = 30000;

// Direction of the qualifying (prev-day) move, from the signed bucket key (up*/dn*).
function qualDir(p: MoverPrediction): "up" | "down" | "flat" {
  if (p.basis?.startsWith("up")) return "up";
  if (p.basis?.startsWith("dn")) return "down";
  return "flat";
}

function DirLean({ p }: { p: MoverPrediction }) {
  if (p.pred_dir === "NONE" || p.p_dir == null)
    return <Badge>coin-flip</Badge>;
  const qd = qualDir(p);
  // A down-day up-lean is a dead-cat BOUNCE, not a fresh long — say so, never a blind LONG.
  const frame =
    p.pred_dir === "LONG" && qd === "down"
      ? "bounce"
      : p.pred_dir === "LONG" && qd === "up"
        ? "cont."
        : p.pred_dir === "SHORT" && qd === "up"
          ? "fade"
          : "";
  // Deliberately a NEUTRAL badge: a tinted pill would give a near-coin-flip more
  // visual weight than we have any right to. Only the arrow carries the sign.
  return (
    <Badge title="Sign-conditioned, measured on a survivor panel — a weak lean, not conviction">
      <span
        className={p.pred_dir === "LONG" ? "pos" : "neg"}
        aria-hidden="true"
      >
        {p.pred_dir === "LONG" ? "▲" : "▼"}
      </span>
      weak lean {p.pred_dir} {probPct(p.p_dir)}
      {frame ? ` (${frame})` : ""}
    </Badge>
  );
}

function Outcome({ p }: { p: MoverPrediction }) {
  if (!p.resolved_ts) return <span className="faint">pending</span>;
  if (p.realized_move_pct == null) return <span className="faint">no data</span>;
  return (
    <span className={p.hit_big5 ? "pos" : "muted"}>
      {p.realized_move_pct >= 0 ? "+" : ""}
      {num(p.realized_move_pct)}% {p.hit_big5 ? "(HIT)" : "(miss)"}
    </span>
  );
}

function moverColumns(): Column<MoverPrediction>[] {
  return [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (p) => (
        <>
          <span className="mono">
            {p.rank != null && <span className="faint tiny">#{p.rank} </span>}
            {p.symbol}
          </span>
          <span className="stack-only num">{probPct(p.p_big5)}</span>
        </>
      ),
    },
    {
      id: "p5",
      header: "P(±5%+)",
      label: "Probability of a ±5% move",
      numeric: true,
      hideOnStack: true,
      cell: (p) => probPct(p.p_big5),
      sortBy: (p) => p.p_big5,
    },
    {
      id: "lift",
      header: "Lift",
      label: "Lift vs base rate",
      numeric: true,
      cell: (p) => (p.lift ? `${p.lift.toFixed(1)}×` : "—"),
      sortBy: (p) => p.lift ?? 0,
    },
    {
      id: "move",
      header: "Typical move",
      label: "Typical move",
      numeric: true,
      cell: (p) => `±${num(p.exp_move_pct, 1)}%`,
    },
    {
      id: "dir",
      header: "Direction",
      label: "Direction lean",
      cell: (p) => <DirLean p={p} />,
    },
    {
      id: "tail",
      header: "Tail split (down / up)",
      label: "Tail split",
      spanOnStack: true,
      cell: (p) => {
        const qd = qualDir(p);
        return (
          <TailSplitBar
            up={p.p_next_up_big}
            down={p.p_next_down_big}
            lead={qd === "up" ? "after UP" : qd === "down" ? "after DOWN" : undefined}
          />
        );
      },
    },
    {
      id: "warn",
      header: "Warnings",
      label: "Warnings",
      spanOnStack: true,
      cell: (p) => (
        <span className="row-wrap">
          {p.tg_mentions ? (
            <Badge
              tone="warning"
              title="Tip-channel mentions in the last 24h — unweighted, unverified; tracked to MEASURE whether mentions carry signal"
            >
              📣 {p.tg_mentions} tip mentions
            </Badge>
          ) : null}
          {p.warn ? <span className="faint small">{p.warn}</span> : null}
          {!p.tg_mentions && !p.warn ? <span className="faint">—</span> : null}
        </span>
      ),
    },
    {
      id: "n",
      header: "Bucket n",
      label: "Bucket sample size",
      numeric: true,
      hideOnStack: true,
      cell: (p) => p.n_bucket,
      sortBy: (p) => p.n_bucket,
    },
  ];
}

export default function MoversPage() {
  const [data, setData] = useState<MoversResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      getMovers(60)
        .then((d) => {
          if (!live) return;
          setData(d);
          setErr(null);
        })
        .catch((e) => live && setErr(String(e)));
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  const sb = data?.scoreboard;
  const history = (data?.history ?? []).filter((r) => r.resolved_ts);
  const today = data?.today ?? [];
  const tradeable = today.filter((p) => p.fillable); // ADV ≥ ₹5cr — actionable
  const research = today.filter((p) => !p.fillable); // circuit-locked, research only
  const columns = moverColumns();

  const historyColumns: Column<MoverPrediction>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (p) => (
        <>
          <span className="mono">{p.symbol}</span>
          <span className="stack-only">
            <Outcome p={p} />
          </span>
        </>
      ),
    },
    { id: "day", header: "Day", label: "Day", cell: (p) => p.for_day, sortBy: (p) => p.for_day },
    {
      id: "p5",
      header: "P(±5%+)",
      label: "Predicted probability",
      numeric: true,
      cell: (p) => probPct(p.p_big5),
    },
    { id: "dir", header: "Direction call", label: "Direction call", cell: (p) => <DirLean p={p} /> },
    {
      id: "realized",
      header: "Realized",
      label: "Realized move",
      numeric: true,
      hideOnStack: true,
      cell: (p) => <Outcome p={p} />,
    },
    {
      id: "dircorrect",
      header: "Dir right?",
      label: "Direction right?",
      numeric: true,
      cell: (p) =>
        p.dir_correct == null ? (
          <span className="faint">—</span>
        ) : p.dir_correct ? (
          <span className="pos">✓ yes</span>
        ) : (
          <span className="neg">✗ no</span>
        ),
    },
    {
      id: "pnl",
      header: "Sleeve P&L",
      label: "Shadow sleeve P&L",
      numeric: true,
      cell: (p) =>
        p.sleeve_pnl_pct == null ? (
          <span className="faint">—</span>
        ) : (
          <span className={signCls(p.sleeve_pnl_pct)}>
            {p.sleeve_pnl_pct >= 0 ? "+" : ""}
            {num(p.sleeve_pnl_pct)}%
          </span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Big-movers watch"
        eyebrow={data?.latest_day ? `Next session · ${data.latest_day}` : "Research sleeve"}
        lede={
          <>
            A <strong>research prediction sleeve</strong>, separate from the paper trading
            book. Every evening it ranks stocks by the <strong>measured</strong>{" "}
            probability of a ±5%+ move next session — 16 years of base rates by
            volatility/momentum bucket, no fitted model.
          </>
        }
        aside={
          <Badge tone="warning" size="lg">
            Shadow book · measure only
          </Badge>
        }
      />

      <Callout tone="warning" icon="⚠" title="Read these three caveats before the table">
        <ul>
          <li>
            Probabilities come from a <strong>survivor panel</strong> — an optimistic
            ceiling, not what you would have got live.
          </li>
          <li>
            <strong>Direction is near-unforecastable.</strong> Every lean below is weak;
            treat it as a coin-flip with a tilt, never as a call.
          </li>
          <li>
            Circuit-band names may be literally <strong>un-buyable</strong>. They are
            split into their own table and never enter the shadow book.
          </li>
        </ul>
      </Callout>

      {err && <ErrorBanner>API unreachable: {err}</ErrorBanner>}
      {!data && !err && <LoadingBlock label="Loading mover predictions" />}

      {sb && (
        <div className="stat-grid">
          <StatTile
            label="Predictions scored"
            value={String(sb.n_resolved)}
            sub={`${sb.n_predictions} made`}
          />
          <StatTile
            label="Hit rate (±5%+)"
            value={probPct(sb.hit_rate_big5)}
            sub={`base rate ${probPct(sb.base_rate_big5, 1)} — skill is the gap, not the level`}
          />
          <StatTile
            label="Direction accuracy"
            value={probPct(sb.dir_accuracy)}
            sub={`${sb.n_dir_called} calls, on hit days only`}
          />
          <StatTile
            label="Shadow sleeve P&L"
            value={`${num(sb.sleeve_cum_pnl_pct)}%`}
            tone={signCls(sb.sleeve_cum_pnl_pct)}
            sub={`${sb.n_sleeve_trades} fillable LONG legs, net of costs · never placed`}
          />
        </div>
      )}

      {data && (
        <>
          <Section
            titleNode={
              <span className="row-wrap">
                Tradeable next session ({tradeable.length})
                <Badge tone="positive">Fillable · ADV ≥ ₹5cr</Badge>
              </span>
            }
            note="The only legs the shadow book takes. Still paper, still never advice."
          >
            <DataTable
              label="Tradeable mover candidates"
              columns={columns}
              rows={tradeable}
              rowKey={(p) => p.symbol}
              empty={
                <EmptyState title="Nothing tradeable next session">
                  {today.length
                    ? "No fillable (ADV ≥ ₹5cr) candidates today."
                    : "No predictions yet — the 16:40 job publishes after each session."}
                </EmptyState>
              }
            />
          </Section>

          {research.length > 0 && (
            <Section
              titleNode={
                <span className="row-wrap">
                  Research only ({research.length})
                  <Badge tone="negative">Cannot fill at circuit · thin ADV</Badge>
                </span>
              }
              note="Ranked for the record. You could not buy these at the circuit price, so they never enter the shadow book and their apparent edge is not realisable."
            >
              <DataTable
                label="Research-only mover candidates"
                columns={columns}
                rows={research}
                rowKey={(p) => p.symbol}
              />
            </Section>
          )}

          <Section
            title={`Track record (${history.length} resolved)`}
            note="Pre-registered: every prediction is written before the session and scored afterwards, hits and misses alike."
          >
            <DataTable
              label="Resolved mover predictions"
              columns={historyColumns}
              rows={history}
              rowKey={(p) => `${p.for_day}-${p.symbol}`}
              tall
              initialSort={{ id: "day", asc: false }}
              empty={
                <EmptyState title="Nothing resolved yet">
                  Outcomes fill in after each session.
                </EmptyState>
              }
            />
          </Section>
        </>
      )}
    </>
  );
}
