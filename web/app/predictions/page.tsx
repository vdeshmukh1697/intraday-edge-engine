"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getPredictions, type Prediction } from "@/lib/api";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Badge,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { DirectionTag, SymbolLink } from "@/components/ui/cells";
import { conf, dayTime, num, pctSigned, signCls } from "@/lib/format";

// The log is append-only, so after the first load we poll only for rows newer than the
// newest id we hold (since_id) — a tiny delta request every few seconds ≈ live.
const POLL_MS = 5000;
const PAGE_SIZE = 300;

const KINDS = [
  { key: "", label: "All" },
  { key: "entry", label: "Entries" },
  { key: "exit", label: "Exits" },
  { key: "skip", label: "Skips" },
  { key: "premarket", label: "Pre-market" },
  { key: "scan", label: "Scan" },
  { key: "health", label: "Health" },
  { key: "halt", label: "Halts" },
];

type Tone = "neutral" | "positive" | "negative" | "warning" | "info";

// Entry/exit/skip must be told apart at a glance; failures must shout.
const KIND_TONE: Record<string, Tone> = {
  entry: "positive",
  exit: "info",
  skip: "warning",
  halt: "negative",
  error: "negative",
};

export default function PredictionsPage() {
  const [rows, setRows] = useState<Prediction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState("");
  const [lastPoll, setLastPoll] = useState<Date | null>(null);
  const maxIdRef = useRef<number>(0);

  const loadFull = useCallback(() => {
    setLoading(true);
    setError(null);
    getPredictions({ limit: PAGE_SIZE })
      .then((d) => {
        setRows(d.predictions);
        maxIdRef.current = d.predictions[0]?.id ?? 0;
        setLastPoll(new Date());
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadFull();
  }, [loadFull]);

  // Live updates: cheap delta poll for rows newer than what we already have.
  useEffect(() => {
    const id = setInterval(() => {
      getPredictions({ since_id: maxIdRef.current, limit: PAGE_SIZE })
        .then((d) => {
          setLastPoll(new Date());
          if (!d.predictions.length) return;
          maxIdRef.current = Math.max(
            maxIdRef.current,
            ...d.predictions.map((p) => p.id)
          );
          // Merge dedup-by-id: dev remounts (StrictMode/Fast Refresh) and reconnect
          // races can deliver rows we already hold — never render a duplicate.
          setRows((prev) => {
            const incoming = new Set(d.predictions.map((p) => p.id));
            return [
              ...d.predictions,
              ...prev.filter((p) => !incoming.has(p.id)),
            ].slice(0, PAGE_SIZE);
          });
        })
        .catch(() => {
          /* transient poll failure — next tick retries */
        });
    }, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const visible = kind ? rows.filter((r) => r.kind === kind) : rows;

  const columns: Column<Prediction>[] = [
    {
      id: "kind",
      header: "Kind",
      primary: true,
      cell: (r) => {
        const t = dayTime(r.ts);
        return (
          <>
            <span className="row-wrap">
              <Badge tone={KIND_TONE[r.kind] ?? "neutral"}>{r.kind}</Badge>
              {r.symbol ? (
                <span className="stack-only">
                  <SymbolLink symbol={r.symbol} />
                </span>
              ) : null}
              {r.delivered === 0 && (
                <Badge tone="warning" title="Alert was logged but not delivered">
                  ⚠ undelivered
                </Badge>
              )}
            </span>
            <span className="stack-only mono tiny faint">
              {t.day} {t.time}
            </span>
          </>
        );
      },
    },
    {
      id: "time",
      header: "Time (IST)",
      label: "Time",
      hideOnStack: true,
      cell: (r) => {
        const t = dayTime(r.ts);
        return (
          <span className="mono" title={r.ts}>
            <span className="faint tiny">{t.day} </span>
            {t.time}
          </span>
        );
      },
      sortBy: (r) => r.id,
    },
    {
      id: "symbol",
      header: "Symbol",
      label: "Symbol",
      hideOnStack: true,
      cell: (r) => (r.symbol ? <SymbolLink symbol={r.symbol} /> : null),
    },
    {
      id: "dir",
      header: "Dir",
      label: "Direction",
      cell: (r) => (r.direction ? <DirectionTag direction={r.direction} /> : null),
    },
    {
      id: "entry",
      header: "Entry",
      label: "Entry",
      numeric: true,
      cell: (r) => (r.entry == null ? null : num(r.entry)),
    },
    {
      id: "stop",
      header: "Stop",
      label: "Stop",
      numeric: true,
      cell: (r) =>
        r.stop_loss == null
          ? null
          : `${num(r.stop_loss)}${r.stop_pct != null ? ` (−${num(r.stop_pct)}%)` : ""}`,
    },
    {
      id: "target",
      header: "Target",
      label: "Target",
      numeric: true,
      cell: (r) =>
        r.target == null
          ? null
          : `${num(r.target)}${r.target_pct != null ? ` (+${num(r.target_pct)}%)` : ""}`,
    },
    {
      id: "rr",
      header: "R:R",
      label: "Risk : reward",
      numeric: true,
      hideOnStack: true,
      cell: (r) => (r.risk_reward == null ? null : num(r.risk_reward, 1)),
    },
    {
      id: "rule",
      header: "Rule score",
      label: "Rule score (uncalibrated)",
      numeric: true,
      hideOnStack: true,
      cell: (r) => (r.confidence == null ? null : conf(r.confidence)),
    },
    {
      id: "qty",
      header: "Qty",
      label: "Qty",
      numeric: true,
      hideOnStack: true,
      cell: (r) => r.qty ?? null,
    },
    {
      id: "result",
      header: "Result",
      label: "Result",
      numeric: true,
      cell: (r) =>
        r.pnl_pct_net == null ? null : (
          <span className={signCls(r.pnl_pct_net)}>
            {pctSigned(r.pnl_pct_net)}
            {r.exit_reason ? <span className="faint small"> {r.exit_reason}</span> : null}
          </span>
        ),
    },
    {
      id: "strategy",
      header: "Strategy",
      label: "Strategy",
      hideOnStack: true,
      cell: (r) => (r.strategy ? <span className="faint small">{r.strategy}</span> : null),
    },
    {
      id: "message",
      header: "Message",
      label: "Message",
      spanOnStack: true,
      cell: (r) => (
        <div className="log-message">
          <div className="clamp-2" title={r.message}>
            {r.message}
          </div>
          {r.reason_plain && (
            <div className="clamp-2 faint tiny" title={r.reason_plain}>
              Why: {r.reason_plain}
            </div>
          )}
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Predictions"
        eyebrow="Append-only alert log"
        lede={
          <>
            Every alert the engine pushes to Telegram, logged at send time with its full
            parameters — entries with entry/stop/target/rule-score/sizing, exits with
            realized P&amp;L, skips the book couldn&apos;t afford, plus pre-market
            briefings, scan picks and health pings. Updates live (~{POLL_MS / 1000}s).{" "}
            <strong>Paper-trading decision support only — no live orders.</strong>
          </>
        }
      />

      {error && <ErrorBanner>Could not load predictions: {error}</ErrorBanner>}

      <div className="control-bar">
        <div className="field" style={{ flex: 1 }}>
          <span id="kind-filter-label">Filter by kind</span>
          <div className="segmented" role="group" aria-labelledby="kind-filter-label">
            {KINDS.map((k) => (
              <button
                key={k.key}
                type="button"
                aria-pressed={kind === k.key}
                onClick={() => setKind(k.key)}
              >
                {k.label}
              </button>
            ))}
          </div>
        </div>
        <span className="faint tiny">
          {visible.length} shown
          {lastPoll ? ` · polled ${lastPoll.toLocaleTimeString("en-IN", { hour12: false })}` : ""}
        </span>
      </div>

      {loading && rows.length === 0 ? (
        <LoadingBlock label="Loading the alert log" rows={6} />
      ) : (
        <Section
          title={kind ? `${KINDS.find((k) => k.key === kind)?.label} (${visible.length})` : `Log (${visible.length})`}
          note="Newest first. New rows arrive at the top without moving what you're reading below them."
        >
          <DataTable
            label="Engine alert log"
            columns={columns}
            rows={visible}
            rowKey={(r) => String(r.id)}
            tall
            empty={
              <EmptyState title="Nothing logged for this filter">
                Rows appear here the moment an alert is sent.
              </EmptyState>
            }
          />
        </Section>
      )}
    </>
  );
}
