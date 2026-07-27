// One implementation of every number/date format the dashboard shows.
// Indian digit grouping throughout (₹1,00,000 — never ₹100,000), fixed decimals
// per kind so table columns align under `font-variant-numeric: tabular-nums`.

const INR0 = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const INR2 = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const GROUP0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** The single dash used for "no value" everywhere. */
export const DASH = "—";

function isMissing(v: number | null | undefined): boolean {
  return v == null || Number.isNaN(v);
}

/** Whole-rupee money: 100000 → "₹1,00,000" (negatives render "-₹512"). */
export function inr(v: number | null | undefined): string {
  return isMissing(v) ? DASH : INR0.format(v as number);
}

/** Per-share price, always 2dp: 2827.5 → "₹2,827.50". */
export function inrPrice(v: number | null | undefined): string {
  return isMissing(v) ? DASH : INR2.format(v as number);
}

/** Signed whole-rupee P&L: "+₹612" / "-₹512". */
export function inrSigned(v: number | null | undefined): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  return `${n > 0 ? "+" : ""}${INR0.format(n)}`;
}

/**
 * Compact rupees for tight slots and axis ticks: 1_25_000 → "₹1.25L",
 * 2_50_00_000 → "₹2.5Cr". Lakh/crore, not K/M — this is an NSE book.
 */
export function inrCompact(v: number | null | undefined): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(abs >= 1e8 ? 0 : 2)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(abs >= 1e6 ? 1 : 2)}L`;
  if (abs >= 1e3) return `${sign}₹${GROUP0.format(Math.round(abs))}`;
  return `${sign}₹${abs.toFixed(0)}`;
}

/** Signed percentage: "+1.23%" / "-0.45%". */
export function pctSigned(v: number | null | undefined, digits = 2): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function pct(v: number | null | undefined, digits = 2): string {
  return isMissing(v) ? DASH : `${(v as number).toFixed(digits)}%`;
}

/** A 0..1 probability as a percentage: 0.37 → "37%". */
export function probPct(v: number | null | undefined, digits = 0): string {
  return isMissing(v) ? DASH : `${((v as number) * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  return isMissing(v) ? DASH : (v as number).toFixed(digits);
}

export function int(v: number | null | undefined): string {
  return isMissing(v) ? DASH : GROUP0.format(Math.round(v as number));
}

/** R-multiple, always signed and 2dp: "+2.00R" / "-1.00R". */
export function rMultiple(v: number | null | undefined): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}R`;
}

/**
 * The uncalibrated rule score, 0–100. Accepts 0..1 or 0..100 — the engine has
 * used both. Deliberately NOT called a confidence or a probability.
 */
export function conf(v: number | null | undefined): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  return `${Math.round(n <= 1 ? n * 100 : n)}`;
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (isMissing(v)) return DASH;
  const n = v as number;
  const s = n.toFixed(digits);
  return n > 0 ? `+${s}` : s;
}

/** Sign class for the green/red convention. Zero is neutral, never green. */
export function signCls(v: number | null | undefined): string {
  if (isMissing(v)) return "";
  const n = v as number;
  return n > 0 ? "pos" : n < 0 ? "neg" : "";
}

/** ▲ / ▼ / · — the non-colour half of the gain/loss signal. */
export function signArrow(v: number | null | undefined): string {
  if (isMissing(v)) return "·";
  const n = v as number;
  return n > 0 ? "▲" : n < 0 ? "▼" : "·";
}

// --- Time ------------------------------------------------------------------
// Backend timestamps are IST wall-clock, sometimes with a space instead of "T"
// (which Safari rejects). Normalise before parsing, and never re-zone them —
// the string already IS the market's local time.

function normalise(ts: string): string {
  return (ts || "").replace(" ", "T");
}

/** Epoch ms for an engine timestamp, or NaN. */
export function parseTs(ts: string | null | undefined): number {
  return Date.parse(normalise(ts ?? ""));
}

/** "HH:MM" from an engine timestamp — the market-hours reading. */
export function hhmm(ts: string | null | undefined): string {
  const s = normalise(ts ?? "");
  return s.length >= 16 ? s.slice(11, 16) : DASH;
}

/** "HH:MM:SS". */
export function hhmmss(ts: string | null | undefined): string {
  const s = normalise(ts ?? "");
  return s.length >= 19 ? s.slice(11, 19) : hhmm(ts);
}

/** "12 Jul · 14:32" — for logs spanning more than one day. */
export function dayTime(ts: string | null | undefined): { day: string; time: string } {
  const ms = parseTs(ts);
  if (!Number.isFinite(ms)) return { day: "", time: ts ?? DASH };
  const d = new Date(ms);
  return {
    day: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
    time: d.toLocaleTimeString("en-IN", { hour12: false }),
  };
}

/** "YYYY-MM-DD" — the trading day an engine timestamp falls on, "" if unparseable.
 *  Plain string slicing on purpose: the timestamp already IS IST wall-clock, so
 *  round-tripping it through Date would re-zone it and shift late trades a day. */
export function isoDay(ts: string | null | undefined): string {
  const s = normalise(ts ?? "");
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : "";
}

/** "22 Jul 2026" from an ISO day — for picking a session out of a list. */
export function dayLabel(isoDate: string): string {
  const ms = Date.parse(`${isoDate}T00:00:00`); // no zone suffix ⇒ parsed as local
  if (!Number.isFinite(ms)) return isoDate;
  return new Date(ms).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** "just now" / "4m ago" / "2h ago" — relative age of a data point. */
export function ago(seconds: number | null | undefined): string {
  if (isMissing(seconds)) return DASH;
  const s = Math.max(0, Math.round(seconds as number));
  if (s < 10) return "just now";
  if (s < 90) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m ago`;
  return `${Math.round(m / 60)}h ago`;
}

/** Clock time of a Date in IST-formatted 24h, for "refreshed …" lines. */
export function clock(d: Date | null | undefined): string {
  return d ? d.toLocaleTimeString("en-IN", { hour12: false }) : DASH;
}
