"use client";

import type { LiveStatus } from "@/lib/api";
import { ago, clock } from "@/lib/format";

// The live indicator is tied to actual feed freshness, never decorative: the dot
// only pulses while the engine is processing bars AND the last bar is recent.
// Stale and offline are different states and say different things, because
// "market closed" and "the feed died" need different reactions from the owner.

export type FeedState = "live" | "stale" | "off";

export function feedStateOf(status: LiveStatus | null): FeedState {
  if (!status?.live) return "off";
  return status.stale ? "stale" : "live";
}

export default function FeedStatus({
  status,
  lastRefresh,
  auto,
  onToggleAuto,
  onRefresh,
  busy,
}: {
  status: LiveStatus | null;
  lastRefresh: Date | null;
  auto?: boolean;
  onToggleAuto?: () => void;
  onRefresh?: () => void;
  busy?: boolean;
}) {
  const state = feedStateOf(status);
  const label =
    state === "live"
      ? `Feed live — last bar ${ago(status?.age_seconds)}`
      : state === "stale"
        ? `Feed stale — last bar ${ago(status?.age_seconds)} (market closed, or the feed dropped)`
        : "Feed offline — no live session running";

  return (
    <div className="feed-status" data-state={state} role="status">
      <span className="feed-dot" aria-hidden="true" />
      <span className="feed-label">{label}</span>
      {status?.live && (
        <span className="feed-meta">
          {status.open_count} open · {status.closed_today} closed today ·{" "}
          {status.watching} watched
        </span>
      )}
      <span className="feed-spacer" />
      <span className="feed-actions">
        {lastRefresh && (
          <span className="faint tiny">refreshed {clock(lastRefresh)}</span>
        )}
        {onToggleAuto && (
          <label className="checkbox tiny">
            <input type="checkbox" checked={!!auto} onChange={onToggleAuto} />
            auto
          </label>
        )}
        {onRefresh && (
          <button
            type="button"
            className="secondary"
            onClick={onRefresh}
            disabled={busy}
          >
            {busy ? "…" : "Refresh"}
          </button>
        )}
      </span>
    </div>
  );
}
