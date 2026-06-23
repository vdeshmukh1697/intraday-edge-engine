// Small formatting helpers shared across pages.

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
