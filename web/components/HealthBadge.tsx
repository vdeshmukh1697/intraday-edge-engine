import type { StrategyHealth } from "@/lib/api";

// Map a status/score to a colour. Prefer the engine's status string;
// fall back to a numeric threshold on the overall score.
function colorFor(health: StrategyHealth): "green" | "amber" | "red" {
  const s = (health.status || "").toLowerCase();
  if (["green", "healthy", "good", "ok"].some((k) => s.includes(k)))
    return "green";
  if (["red", "unhealthy", "bad", "poor", "critical"].some((k) => s.includes(k)))
    return "red";
  if (["amber", "yellow", "warn", "caution", "degraded"].some((k) => s.includes(k)))
    return "amber";
  // Numeric fallback (overall assumed 0..100; tolerate 0..1).
  const v = health.overall > 1 ? health.overall : health.overall * 100;
  if (v >= 70) return "green";
  if (v >= 45) return "amber";
  return "red";
}

export default function HealthBadge({ health }: { health: StrategyHealth }) {
  const color = colorFor(health);
  const score =
    health.overall > 1 ? Math.round(health.overall) : Math.round(health.overall * 100);
  return (
    <span className="health-badge" title={`Window trades: ${health.window_trades}`}>
      <span className={`health-dot ${color}`} />
      <span>
        <span className="health-score">{score}</span>
        <span className="subtle"> / 100</span>
      </span>
      <span className="health-status">{health.status}</span>
    </span>
  );
}
