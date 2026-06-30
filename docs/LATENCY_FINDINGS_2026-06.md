# Platform latency — measured, fixed, and what's left (2026-06-30)

Measured **live during market hours** (≈14:30–15:10 IST, NSE open) so the numbers are real, not
synthetic. Repo `feat/full-nse-realtime-pipeline`. Paper-only; no order paths touched.

## What I measured (before)
| Hop | Result | Notes |
|---|---|---|
| Local API compute | `/healthz` 3 ms · `/api/live/status` 5–7 ms · `/api/paper/analytics` 20–22 ms | backend compute is **not** a bottleneck |
| Cloudflare **quick-tunnel** hop | local 3 ms vs tunnel **~110–145 ms** steady; **~880 ms** on the cold first request | per round-trip; the WS stream pays this **once** (persistent connection), REST polls pay it every call |
| Dhan **quote REST** (`marketfeed/ltp`) | effective **~1 req/s** | a lone `/ws/quotes` connection streams cleanly at **~1.11 s** cadence, full 40 quotes, 0 % throttled |
| `/ws/quotes` cold start | **~3.4 s** before the first tick | every connection ran `build_broker`, which **re-downloads the scrip master** each time |
| Concurrency | a **2nd** concurrent REST consumer was **throttled 16/16** (DH-904/429) | watchlist + a stock chart, or two tabs, collide → dropped ticks → multi-second effective price latency |
| Frontend render | negligible | lightweight-charts does incremental `update()`; pages also poll REST every 15 s |

## Biggest contributors, ranked
1. **Per-connection Dhan REST polling** (dominant, fixable in code). Each `/ws/quotes` connection
   built its own broker and polled Dhan independently. Dhan's quote REST is ~1 req/s, so the 2nd
   concurrent consumer got 429'd and the dashboard silently skipped that update — the live price
   stalled for several seconds. Plus the ~3.4 s per-connection cold start.
2. **Cloudflare quick-tunnel hop** (~110–145 ms steady, ~880 ms cold). The live WS already
   amortizes this (one persistent upgrade), so it is **not** the live-price bottleneck — it taxes
   the 15 s REST polls (status / analytics / history) and the first paint.
3. **Client push interval** (1 s floor) — inherent to a polling design.

## What I fixed — shared in-process quote hub (`_QuoteHub`, `signal_engine/api/app.py`)
One background task polls the **union** of every connection's symbols once per second; all
connections read a warm shared cache. The broker is built **once** and shared (its `urllib` HTTP is
stateless, so the poll loop and the intraday endpoint use it concurrently).

**After (same live conditions, 3 concurrent connections — watchlist + ADANIENT + RELIANCE):**
| Metric | Before | After |
|---|---|---|
| Throttled polls (2nd+ consumer) | 16/16 (100 %) | **0/30 (0 %)** |
| First tick after connect | ~3.4 s | **~0.12 s** (warm cache) |
| Dhan polls for N connections | N (collide) | **1** |
| Cadence | ~1.11 s (1 conn only) | **1.00 s** (all 3, clean) |

The live **paper engine is unaffected** — it uses the separate Dhan **WS binary feed**, not REST,
so this never touches the trading path.

Bonus: `/api/intraday/{symbol}` (used to seed the live chart) reuses the hub's shared broker and is
TTL-cached (45 s), so it doesn't reintroduce REST-quota pressure.

## What I deliberately did NOT do (and why)
- **Switch `/ws/quotes` to the Dhan WS binary feed** (would give sub-second ticks). Dhan limits
  concurrent feed connections and the live paper engine already holds one; a second feed from the
  API risks contending with — and breaking — paper trading. The REST hub is the safe, sufficient
  win (the bottleneck was *redundant polling*, not the 1 s cadence itself). Documented as a future
  upgrade if sub-second display is ever needed (gate it behind off-hours / single-connection use).
- **Named Cloudflare tunnel** (removes the ~110–145 ms hop variance + ~880 ms cold start, and the
  per-restart URL churn). It's a manual one-time setup (`cloudflared tunnel login` + an owned
  domain) — can't be scripted headlessly. **Recommended** as the next durable ops upgrade.
- **Unify the dashboard's 15 s REST polls** — low ROI; the tunnel hop dominates there, not count.

## How to re-measure
- Dhan REST round-trip / throttling: `scratchpad/probe_quote.py` (isolated) — note it competes with
  any open dashboard `/ws/quotes`, which is the point.
- Concurrent-connection proof: `scratchpad/concurrent_ws.py` (3 parallel `/ws/quotes`, reports
  first-tick + warn ratio). Run during market hours.
- Tunnel vs local: `curl -w '%{time_total}'` on `/healthz` locally and via the tunnel URL.
