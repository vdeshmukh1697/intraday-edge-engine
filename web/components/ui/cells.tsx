import type { ReactNode } from "react";
import Link from "next/link";
import {
  DASH,
  inr,
  inrPrice,
  inrSigned,
  pctSigned,
  pct as fmtPct,
  signArrow,
  signCls,
} from "@/lib/format";

// Value renderers shared by every table and tile. Colour is never the only
// signal — signed figures carry an explicit + / − or an arrow glyph.

export function Money({
  value,
  kind = "whole",
  sub,
}: {
  value: number | null | undefined;
  /** whole = ₹1,00,000 · price = ₹2,827.50 · signed = +₹612 (tinted) */
  kind?: "whole" | "price" | "signed";
  sub?: ReactNode;
}) {
  if (value == null || Number.isNaN(value)) return <span className="faint">{DASH}</span>;
  if (kind === "signed") {
    return (
      <span className={signCls(value)}>
        {inrSigned(value)}
        {sub}
      </span>
    );
  }
  return (
    <span>
      {kind === "price" ? inrPrice(value) : inr(value)}
      {sub}
    </span>
  );
}

export function Pct({
  value,
  signed = false,
  digits = 2,
  tint = false,
}: {
  value: number | null | undefined;
  signed?: boolean;
  digits?: number;
  tint?: boolean;
}) {
  if (value == null || Number.isNaN(value)) return <span className="faint">{DASH}</span>;
  const text = signed ? pctSigned(value, digits) : fmtPct(value, digits);
  return <span className={tint ? signCls(value) : undefined}>{text}</span>;
}

/**
 * A gain/loss figure with its arrow. The arrow is the redundant, colour-blind-safe
 * half of the signal, so it is never dropped.
 */
export function Delta({
  value,
  format,
  className = "",
}: {
  value: number | null | undefined;
  format: (v: number | null | undefined) => string;
  className?: string;
}) {
  if (value == null || Number.isNaN(value))
    return <span className="faint">{DASH}</span>;
  return (
    <span className={`${signCls(value)} ${className}`.trim()}>
      <span className="delta-arrow" aria-hidden="true">
        {signArrow(value)}
      </span>{" "}
      {format(value)}
    </span>
  );
}

export function SymbolLink({ symbol, date }: { symbol: string; date?: string }) {
  const href = `/stock/${encodeURIComponent(symbol)}${date ? `?date=${date}` : ""}`;
  return (
    <Link href={href} className="mono">
      {symbol}
    </Link>
  );
}

/** LONG / SHORT. Direction is a category, so it gets a label, not just a colour. */
export function DirectionTag({ direction }: { direction: string | null | undefined }) {
  if (!direction) return <span className="faint">{DASH}</span>;
  const long = direction.toUpperCase() === "LONG";
  return (
    <span className="badge" data-tone={long ? "positive" : "negative"}>
      {long ? "▲" : "▼"} {direction}
    </span>
  );
}

export function Missing() {
  return <span className="faint">{DASH}</span>;
}
