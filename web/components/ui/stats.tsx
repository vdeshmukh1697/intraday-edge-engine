"use client";

import { useId, useState, type ReactNode } from "react";
import { probPct, signCls } from "@/lib/format";

// Stat tiles, hero figures, meters and the small dependency-free charts.
// Form follows the data's job (dataviz skill): a single current value is a tile,
// not a one-bar chart; a ratio against a limit is a meter; sign-split magnitude
// is a diverging bar.

export function StatTile({
  label,
  value,
  sub,
  tone,
  emphasis,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  /** "pos" | "neg" | "" — from signCls(), so tint always matches the sign. */
  tone?: string;
  /** "quiet" shrinks the value for supporting figures. */
  emphasis?: "quiet";
}) {
  return (
    <div className="stat-tile" data-emphasis={emphasis}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value${tone ? ` ${tone}` : ""}`}>{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

/** The one number a page leads with. */
export function Hero({
  label,
  value,
  tone,
  children,
}: {
  label: ReactNode;
  value: string;
  tone?: string;
  /** The delta line under the figure. */
  children?: ReactNode;
}) {
  return (
    <div className="hero">
      <span className="stat-label">{label}</span>
      <span className={`hero-value${tone ? ` ${tone}` : ""}`}>{value}</span>
      {children && <span className="hero-delta">{children}</span>}
    </div>
  );
}

/** One ratio against a limit. Same-hue track, value always written out. */
export function Meter({
  name,
  value,
  ratio,
}: {
  name: string;
  value: string;
  /** 0..1, clamped. */
  ratio: number;
}) {
  const width = Math.max(0, Math.min(100, ratio * 100));
  return (
    <div className="meter">
      <div className="meter-name">{name}</div>
      <div className="meter-value">{value}</div>
      <div
        className="bar-track"
        role="meter"
        aria-valuenow={Math.round(width)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={name}
      >
        <div className="bar-fill" style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

export interface BarRow {
  key: string;
  label: string;
  value: number;
  valueText: string;
  meta?: string;
}

/** Horizontal magnitude bars, signed. Label + value are always visible. */
export function BarRows({ rows }: { rows: BarRow[] }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
  return (
    <div className="bar-rows">
      {rows.map((r) => (
        <div className="bar-row" key={r.key}>
          <div className="bar-row-head">
            <span>{r.label}</span>
            <span className={`bar-row-inline ${signCls(r.value)}`}>
              {r.valueText}
              {r.meta ? ` · ${r.meta}` : ""}
            </span>
          </div>
          <div className="bar-track">
            <div
              className="bar-fill"
              data-sign={r.value > 0 ? "pos" : r.value < 0 ? "neg" : undefined}
              style={{ width: `${(Math.abs(r.value) / max) * 100}%` }}
            />
          </div>
          <div className={`bar-row-value ${signCls(r.value)}`}>
            {r.valueText}
            {r.meta ? ` · ${r.meta}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Per-trade P&L distribution. Sign-split (diverging about zero), 2px gaps
 * between bars, hover tooltip per bar.
 */
export function Histogram({
  bins,
  fmt,
}: {
  bins: { lo: number; hi: number; count: number }[];
  fmt: (n: number) => string;
}) {
  const max = Math.max(1, ...bins.map((b) => b.count));
  return (
    <div className="histogram">
      {bins.map((b, i) => (
        <div
          className="histogram-col"
          key={i}
          title={`${fmt(b.lo)} to ${fmt(b.hi)}: ${b.count} trade${b.count === 1 ? "" : "s"}`}
        >
          <span
            className="histogram-bar"
            data-sign={b.hi <= 0 ? "neg" : "pos"}
            style={{ height: `${(b.count / max) * 100}%` }}
          />
          <span className="histogram-x">{Math.round((b.lo + b.hi) / 2)}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Sign-conditioned next-day tail split: two arms diverging from a neutral centre.
 * The percentages and the ↑/↓ glyphs stay on screen — the bar is the redundant
 * channel, never the only one.
 */
export function TailSplitBar({
  up,
  down,
  lead,
}: {
  up: number | null;
  down: number | null;
  /** "after UP: " / "after DOWN: " — what the split is conditioned on. */
  lead?: string;
}) {
  if (up == null || down == null) return <span className="faint">—</span>;
  const scale = Math.max(up, down, 0.01);
  return (
    <span
      className="tailsplit"
      title="P(next ≥ +5%) / P(next ≤ −5%) — the honest direction split, not a blended P(up)"
    >
      {lead && <span className="faint">{lead}</span>}
      <span className="neg">{probPct(down)}↓</span>
      <span className="tailsplit-track" aria-hidden="true">
        <span
          className="tailsplit-arm"
          data-dir="down"
          style={{ width: `${(down / scale) * 45}%` }}
        />
        <span
          className="tailsplit-arm"
          data-dir="up"
          style={{ width: `${(up / scale) * 45}%` }}
        />
      </span>
      <span className="pos">{probPct(up)}↑</span>
    </span>
  );
}

/**
 * Dependency-free line chart with a crosshair + value readout on hover.
 * Used where lightweight-charts would be overkill (paper analytics).
 */
export function LineChart({
  points,
  fmt,
  labels,
  height = 180,
  label,
}: {
  points: number[];
  fmt: (n: number) => string;
  /** X-axis descriptions, same length as points; used in the readout. */
  labels?: string[];
  height?: number;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const clipId = useId();
  const W = 760;
  const H = height;
  const PAD_X = 8;
  const PAD_Y = 16;

  if (points.length < 2) {
    return <p className="faint small">Not enough points yet to draw a curve.</p>;
  }

  const min = Math.min(0, ...points);
  const max = Math.max(0, ...points);
  const x = (i: number) => PAD_X + (i / (points.length - 1)) * (W - 2 * PAD_X);
  const y = (v: number) => H - PAD_Y - ((v - min) / (max - min || 1)) * (H - 2 * PAD_Y);
  const path = points
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
    .join(" ");
  const zero = y(0);
  const last = points[points.length - 1];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    const i = Math.round(frac * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const active = hover ?? points.length - 1;

  return (
    <figure className="stack-tight" style={{ margin: 0 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="svg-chart"
        role="img"
        aria-label={`${label}. Latest ${fmt(last)}, range ${fmt(min)} to ${fmt(max)}.`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <clipPath id={clipId}>
          <rect x="0" y="0" width={W} height={H} />
        </clipPath>
        <line x1={PAD_X} y1={zero} x2={W - PAD_X} y2={zero} className="baseline" />
        <path d={path} className="line" clipPath={`url(#${clipId})`} />
        {hover != null && (
          <g>
            <line
              x1={x(active)}
              y1={PAD_Y / 2}
              x2={x(active)}
              y2={H - PAD_Y / 2}
              className="grid"
            />
            <circle
              cx={x(active)}
              cy={y(points[active])}
              r={4}
              fill="var(--series-1)"
              stroke="var(--surface)"
              strokeWidth={2}
            />
          </g>
        )}
        <text x={PAD_X} y={12} className="tick">
          {fmt(max)}
        </text>
        <text x={PAD_X} y={H - 4} className="tick">
          {fmt(min)}
        </text>
      </svg>
      <figcaption className="small muted">
        {hover != null ? (
          <>
            {labels?.[active] ? `${labels[active]} · ` : ""}
            <strong>{fmt(points[active])}</strong>
          </>
        ) : (
          <>
            Latest <strong>{fmt(last)}</strong> · {points.length} points · hover for a
            value
          </>
        )}
      </figcaption>
    </figure>
  );
}

/** Filled area below a baseline — drawdown is always ≤ 0, so it reads downward. */
export function AreaChart({
  points,
  fmt,
  label,
  height = 110,
}: {
  points: number[];
  fmt: (n: number) => string;
  label: string;
  height?: number;
}) {
  if (points.length < 2) {
    return <p className="faint small">Not enough points yet.</p>;
  }
  const W = 760;
  const H = height;
  const PAD_X = 8;
  const PAD_Y = 14;
  const min = Math.min(0, ...points);
  const max = Math.max(0, ...points);
  const x = (i: number) => PAD_X + (i / (points.length - 1)) * (W - 2 * PAD_X);
  const y = (v: number) => H - PAD_Y - ((v - min) / (max - min || 1)) * (H - 2 * PAD_Y);
  const d =
    `M${x(0)} ${y(0)} ` +
    points.map((v, i) => `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ") +
    ` L${x(points.length - 1)} ${y(0)} Z`;
  return (
    <figure style={{ margin: 0 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="svg-chart"
        role="img"
        aria-label={`${label}. Worst ${fmt(min)}.`}
      >
        <path d={d} className="area-neg" />
        <line x1={PAD_X} y1={y(0)} x2={W - PAD_X} y2={y(0)} className="baseline" />
        <text x={PAD_X} y={H - 2} className="tick">
          worst {fmt(min)}
        </text>
      </svg>
    </figure>
  );
}
