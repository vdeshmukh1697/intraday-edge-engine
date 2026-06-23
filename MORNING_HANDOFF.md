# Morning Handoff — Ready to Paper Trade

Everything below was set up overnight to run autonomously. Decision-support only — the tool
places **no live orders**; it paper-trades and alerts.

## Where to look first
- **Live metrics report:** `logs/MORNING_REPORT.md` (auto-generated when the overnight pipeline
  finishes — backfill coverage, ML metrics, backtest results, service status).
- **Dashboard:** https://web-beta-beige-60.vercel.app (real NSE names + real charts).

## What runs automatically today (IST, scheduler is live)
- **06:00** token renew attempt (portal tokens can't auto-renew — see Token note).
- **08:00** morning data gather — archive the prior session's bars for the whole NSE universe.
- **08:30** pre-market briefing (global cues + news) → Telegram.
- **09:15 → 15:30** LIVE paper trading — streams the Dhan feed for the watchlist, runs the
  full signal→risk→paper-trade pipeline, and alerts entries/exits via Telegram.
- **15:45** end-of-day full-universe scan → top picks. **16:10** nightly archive.

## What was built/verified overnight
1. **5-year history** for the NSE universe (Dhan minute bars) → `data/parquet/` (consolidated
   per symbol/year). Gap-fill + straggler sweeps completed the coverage.
2. **ML scorer trained on the REAL corpus** (`data/models/signal_model.json`) — beats the rules
   baseline out-of-sample. Use `signal-engine scan --ml` for shadow scoring.
3. **Dashboard serves real data** — leaderboard scans the real archive (auto-refreshes as the
   corpus grows); charts use real Dhan/Yahoo bars.
4. **Paper-trading machinery verified** — `signal-engine backtest` (metrics, health) and the
   live runner. Trade mechanics audited: no look-ahead, pessimistic stop-first fills, realistic
   NSE intraday costs, edge-after-cost gate.

## Honest findings (trading-logic review)
- The trade **mechanics are correct and conservative**. The base **rules strategy has thin/negative
  edge** after costs (≈34% of signals reach T1 before stop on real data; slightly negative on the
  synthetic backtest). This is normal for raw intraday trend-following — the **ML filter and forward
  paper-trading are the edge discipline**. Treat signals as decision-support; validate forward.
- Live paper trades currently surface via **Telegram alerts**; a live-positions dashboard panel is a
  future enhancement (the backtest page shows historical paper performance).

## Token note (action maybe needed)
The Dhan access token is valid through **~19:32 IST today**, covering the full 09:15–15:30 session.
If you trade past that or on a later day, the dashboard will show **"Reconnect Dhan"** — one tap +
OTP refreshes it. Portal tokens can't be auto-renewed (Dhan limitation, documented).

## Manual commands (if you want to drive it yourself)
```
signal-engine live              # stream Dhan + paper-trade now (market hours)
signal-engine scan --ml         # full-universe scan with ML shadow scoring
signal-engine backtest --days 20
signal-engine train --source archive --max-symbols 400   # retrain on real corpus
```
Stop background services: `pkill -f "cli serve"; pkill -f "cloudflared tunnel"; pkill -f "cli schedule"`
