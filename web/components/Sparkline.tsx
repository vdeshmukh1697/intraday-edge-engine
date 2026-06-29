"use client";

// Tiny dependency-free sparkline: an SVG polyline of the recent live LTPs for one symbol.
// Green if the series is up over the window, red if down. Renders nothing until 2+ points.
export function Sparkline({
  points,
  width = 88,
  height = 26,
}: {
  points: number[];
  width?: number;
  height?: number;
}) {
  if (!points || points.length < 2) {
    return <svg width={width} height={height} aria-hidden="true" />;
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
  const up = points[points.length - 1] >= points[0];
  const color = up ? "var(--pos, #16a34a)" : "var(--neg, #dc2626)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <polyline
        points={coords}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
