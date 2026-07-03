// Small formatting helpers shared across pages.

// Indian-locale rupee formatters (lakh/crore grouping): whole rupees for
// portfolio-level money, 2dp for per-share prices.
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

/** Whole-rupee money: 100000 → "₹1,00,000" (negatives render "-₹512"). */
export function inr(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return INR0.format(v);
}

/** Per-share price, always 2dp: 2827.5 → "₹2,827.50". */
export function inrPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return INR2.format(v);
}

/** Signed whole-rupee P&L: "+₹612" / "-₹512" — for green/red money figures. */
export function inrSigned(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${INR0.format(v)}`;
}

/** Signed percentage: "+1.23%" / "-0.45%". */
export function pctSigned(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** CSS class by sign — matches the green/red convention used across pages. */
export function signCls(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}

export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function int(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return Math.round(v).toLocaleString();
}

export function conf(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  // Accept 0..1 or 0..100.
  const x = v <= 1 ? v * 100 : v;
  return `${Math.round(x)}%`;
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}
