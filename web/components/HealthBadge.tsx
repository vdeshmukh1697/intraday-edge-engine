import type { StrategyHealth } from "@/lib/api";

// Map a status/score to a tone. Prefer the engine's status string;
// fall back to a numeric threshold on the overall score.
function toneFor(health: StrategyHealth): "positive" | "warning" | "negative" {
  const s = (health.status || "").toLowerCase();
  if (["green", "healthy", "good", "ok"].some((k) => s.includes(k))) return "positive";
  if (["red", "unhealthy", "bad", "poor", "critical"].some((k) => s.includes(k)))
    return "negative";
  if (["amber", "yellow", "warn", "caution", "degraded"].some((k) => s.includes(k)))
    return "warning";
  // Numeric fallback (overall assumed 0..100; tolerate 0..1).
  const v = health.overall > 1 ? health.overall : health.overall * 100;
  if (v >= 70) return "positive";
  if (v >= 45) return "warning";
  return "negative";
}

/** Status is never colour alone: the score and the status word are both written out. */
export default function HealthBadge({ health }: { health: StrategyHealth }) {
  const tone = toneFor(health);
  const score =
    health.overall > 1 ? Math.round(health.overall) : Math.round(health.overall * 100);
  return (
    <span
      className="badge"
      data-tone={tone}
      data-size="lg"
      title={`Composite of hit rate, profit factor, expectancy, calibration and drawdown over ${health.window_trades} trades. Descriptive, not predictive.`}
    >
      Health {score}/100 · {health.status}
    </span>
  );
}
