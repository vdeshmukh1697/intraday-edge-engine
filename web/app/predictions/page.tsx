"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getPredictions, type Prediction } from "@/lib/api";

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

const KIND_CLS: Record<string, string> = {
  entry: "pos",
  exit: "",
  skip: "",
  halt: "neg",
  error: "neg",
};

const fmt = (n: number | null | undefined, digits = 2) =>
  n == null ? "—" : n.toFixed(digits);

function istTime(ts: string): { day: string; time: string } {
  try {
    const d = new Date(ts);
    return {
      day: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
      time: d.toLocaleTimeString("en-IN", { hour12: false }),
    };
  } catch {
    return { day: "", time: ts };
  }
}

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

  useEffect(() => { loadFull(); }, [loadFull]);

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
        .catch(() => { /* transient poll failure — next tick retries */ });
    }, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (error)
    return <div className="card">Could not load predictions: {error}</div>;
  if (loading && rows.length === 0) return <div className="card">Loading…</div>;

  const visible = kind ? rows.filter((r) => r.kind === kind) : rows;

  return (
    <div className="predictions">
      <div className="page-head">
        <h1>Predictions</h1>
        <p className="muted">
          Every alert the engine pushes to Telegram, logged at send time with its full
          parameters — entries with entry/stop/target/confidence/sizing, exits with realized
          P&amp;L, skips the book couldn&apos;t afford, plus pre-market briefings, scan picks and
          health pings. Updates live (~{POLL_MS / 1000}s). Paper-trading decision support only —
          no live orders.
        </p>
      </div>

      <div className="stats-strip">
        {KINDS.map((k) => (
          <button
            key={k.key}
            className={`tag small${kind === k.key ? " pos" : ""}`}
            onClick={() => setKind(k.key)}
            style={{ cursor: "pointer" }}
          >
            {k.label}
          </button>
        ))}
        <span className="live-spacer" />
        {lastPoll && (
          <span className="muted small">
            updated {lastPoll.toLocaleTimeString("en-IN")} · {visible.length} shown
          </span>
        )}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time (IST)</th>
              <th>Kind</th>
              <th>Symbol</th>
              <th>Dir</th>
              <th>Entry</th>
              <th>Stop</th>
              <th>Target</th>
              <th>R:R</th>
              <th>Conf</th>
              <th>Qty</th>
              <th>Result</th>
              <th>Strategy</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const t = istTime(r.ts);
              return (
                <tr key={r.id}>
                  <td title={r.ts}>
                    <span className="muted small">{t.day}</span> {t.time}
                  </td>
                  <td>
                    <span className={`tag small ${KIND_CLS[r.kind] ?? ""}`}>
                      {r.kind}
                      {r.delivered === 0 ? " ⚠︎" : ""}
                    </span>
                  </td>
                  <td>
                    {r.symbol ? (
                      <Link href={`/stock/${r.symbol}`}>{r.symbol}</Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={r.direction === "LONG" ? "pos" : r.direction === "SHORT" ? "neg" : ""}>
                    {r.direction ?? "—"}
                  </td>
                  <td>{fmt(r.entry)}</td>
                  <td>
                    {r.stop_loss != null
                      ? `${fmt(r.stop_loss)}${r.stop_pct != null ? ` (−${fmt(r.stop_pct)}%)` : ""}`
                      : "—"}
                  </td>
                  <td>
                    {r.target != null
                      ? `${fmt(r.target)}${r.target_pct != null ? ` (+${fmt(r.target_pct)}%)` : ""}`
                      : "—"}
                  </td>
                  <td>{fmt(r.risk_reward, 1)}</td>
                  <td>{r.confidence != null ? r.confidence.toFixed(0) : "—"}</td>
                  <td>{r.qty ?? "—"}</td>
                  <td className={r.pnl_pct_net != null ? (r.pnl_pct_net >= 0 ? "pos" : "neg") : ""}>
                    {r.pnl_pct_net != null
                      ? `${r.pnl_pct_net >= 0 ? "+" : ""}${fmt(r.pnl_pct_net)}%${
                          r.exit_reason ? ` ${r.exit_reason}` : ""
                        }`
                      : "—"}
                  </td>
                  <td className="muted small">{r.strategy ?? "—"}</td>
                  <td className="muted small" style={{ maxWidth: 360 }}>
                    <div
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={r.message}
                    >
                      {r.message}
                    </div>
                    {/* Plain-English reason, when the alert carried one. */}
                    {r.reason_plain && (
                      <div
                        className="why-sub"
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={r.reason_plain}
                      >
                        Why: {r.reason_plain}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={13} className="muted">
                  No predictions logged yet — rows appear here the moment an alert is sent.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
