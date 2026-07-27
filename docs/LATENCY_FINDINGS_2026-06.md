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

## 2026-07-02 follow-up audit

**Measurement caveat: market CLOSED** (probed ≈04:00–04:30 IST). `/ws/quotes` holds the last traded
price, so WS numbers verify the hub/push path, NOT market-hours Dhan throttling (that was proven
2026-06-30). Probes: `scratchpad/probe_rest_2026-07-02.sh`, `scratchpad/probe_ws_2026-07-02.py`.

### Current state (before this pass)
| Hop | Measured | Notes |
|---|---|---|
| Local API compute (p50 of 8) | `/healthz` 2 ms · `/api/live/status` 3 ms · `/api/watchlist` 12 ms · `/api/intraday/RELIANCE` 11 ms · `/api/paper/analytics` 18 ms · `/api/paper/trades` 24 ms | still not the bottleneck |
| **`/api/premarket`** | **2.3–3.6 s per call, EVERY call** | re-fetches Yahoo cues + RSS news per request; slowest page by far |
| Tunnel hop, warm connection | ~75–120 ms steady, jitter spikes to ~340 ms | fresh-`curl` totals of ~1.15 s are ~1.03 s DoH DNS bootstrap (probe artifact — browsers cache DNS); TLS-to-TTFB delta ≈ 86 ms |
| Tunnel compression | CF edge **already gzips** REST responses (observed `content-encoding: gzip` via tunnel, none locally) | server-side GZipMiddleware would be redundant |
| `/ws/quotes` local | open 27 ms · first tick 129 ms (40 quotes) · cadence 1.00 s clean | hub fix from 06-30 holding |
| `/ws/quotes` tunnel | first tick 710 ms incl. DNS+TLS+upgrade (paid once) · cadence ~0.96 s | fine |
| Vercel dashboard | warm TTFB 35–44 ms · page HTML ~6 KB · watchlist route JS = **148 KB gzipped** (9 chunks) | healthy; nothing to fix |
| AuthGate (frontend) | blanks the whole app until `/api/auth/status` returns → serialized ~90–120 ms warm / ~1 s cold **before any content or data fetch, on every page load** | structural waterfall |

### Fixed (working tree, uncommitted; verified on a separate uvicorn @8010)
| Change | Before | After |
|---|---|---|
| `/api/premarket` TTL cache (300 s, keyed by full param set, bounded 32 entries) — `signal_engine/api/app.py` | 2.3–3.6 s every call | **~1.5 ms** on hit (first call per param set still pays compute) |
| AuthGate optimistic render — `web/components/AuthGate.tsx` | blank screen + all page fetches serialized behind the auth round-trip (~120 ms warm, ~1 s cold) | app renders immediately; gate still appears if the token is actually expired |

Tests: `tests/test_api.py` 20/20 pass; `web` `tsc --noEmit` clean.

### Rejected (measured first)
- **GZipMiddleware / payload trimming** — CF edge already compresses the tunnel path; payloads are
  ≤43 KB and the hop is RTT-bound, not bandwidth-bound. Zero win.
- **uvicorn keep-alive / worker tuning** — cloudflared→uvicorn is localhost (sub-ms reconnect);
  browser→CF keep-alive is CF-managed. ⚠️ **Never set `--workers >1`**: the in-process `_QuoteHub`
  and leaderboard caches assume one process — N workers = N Dhan pollers = the 429 bug back.
- **Micro-TTL caches on `/api/watchlist` / `/api/paper/*` / `/api/live/status`** — saves 3–24 ms
  against a ~120 ms tunnel RTT (≤20 %) and adds staleness to live paper views. Low ROI.
- **next.config.js changes** — nothing harmful present; Vercel compresses; bundles are small.

### Remaining, ranked by user impact
1. **Named Cloudflare tunnel** (unchanged from 06-30): removes hop variance (~120 ms steady,
   ~340 ms jitter spikes, ~1 s cold TLS) and per-restart URL churn. Manual `cloudflared tunnel login`
   — no cert at `~/.cloudflared/cert.pem`, so still skipped.
2. **`/api/backtest` is broken/very slow**: the RUNNING (stale) process 500s on it (old
   `send_alert` ImportError — already fixed in source; clears at the next coordinated restart).
   On current code it runs a **full 10-day synthetic backtest per request, uncached (>60 s)**.
   Needs a cache or precompute before the backtest page is usable; not a "safe quick fix", so left.
   > **Superseded 2026-07-27.** Done. The scans moved to a subprocess behind a single-flight,
   > serve-stale TTL cache (backtest 6 h), then onto their own lane so a backtest cannot starve
   > the read path; `days` is capped at 30. Measured cost was **623 s** for `days=10`, not >60 s.
   > Current behaviour: `docs/MORNING_HANDOFF.md` (2026-07-26 / 2026-07-27 entries).
3. **15 s REST polls** — each poll costs one warm-connection tunnel RTT (~90–120 ms); fine.
