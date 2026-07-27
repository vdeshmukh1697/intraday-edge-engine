"use client";

// Dependency-free sparkline: an SVG polyline of the recent live LTPs for one
// symbol. Colour follows the sign of the window's move, and the accessible label
// spells that out — the line is never the only cue.
export function Sparkline({
  points,
  label,
}: {
  points: number[];
  /** What the line is (e.g. "RELIANCE, last 40 ticks"). */
  label?: string;
}) {
  const width = 88;
  const height = 26;
  if (!points || points.length < 2) {
    return (
      <svg
        className="sparkline"
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
      />
    );
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1; // avoid /0 on a flat line
  const stepX = width / (points.length - 1);
  const coords = points
    .map((p, i) => {
      const x = i * stepX;
      const y = height - 1 - ((p - min) / span) * (height - 2); // 1px top/bottom padding
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const move = points[points.length - 1] - points[0];
  // A flat window (the market is closed and the feed is holding one price) must
  // not read as a gain — it gets the neutral ink, not green.
  const stroke =
    move > 0 ? "var(--positive)" : move < 0 ? "var(--negative)" : "var(--text-tertiary)";
  const direction = move > 0 ? "up" : move < 0 ? "down" : "flat";
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${label ? `${label}: ` : ""}${direction} ${Math.abs(move).toFixed(2)} over the window`}
    >
      <polyline
        points={coords}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
