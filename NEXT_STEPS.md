# Next Steps

Status of the path from "built & tested" to "running on real data." See PLAN.md / PROGRESS.md.

## ✅ Done (no credentials needed) — built and committed
- **Real news ingestion** — `RSSNewsProvider` (Moneycontrol/ET RSS, live-verified). Enable: `SE_NEWS_SOURCE=rss`.
- **Real global cues** — `YahooCuesProvider` (yfinance, live-verified). Enable: `SE_CUES_SOURCE=yahoo`.
- **Dhan adapter** — written + unit-tested (historical/quote normalization, instrument-master mapping). Ready to flip on with a token.
- **Instrument master loader** — `DhanInstrumentMaster` (symbol → security_id from Dhan's free scrip CSV).
- **Deployment** — `Dockerfile` + `docker-compose.yml` (API + scheduler), persists `data/`.
- **Daily scheduler** — `signal-engine schedule` (pre-market 08:30 / market-hours scan / nightly archive, IST, skips holidays).
- **Alerting** — WhatsApp + Telegram alerters (env-gated, tested mocked).
- **FastAPI API** + **Next.js dashboard** scaffold (`web/`).

## ⛔ Blocked on YOU (only you can do these)
1. **Dhan API token** — *KYC in review.* Once approved: web.dhan.co → Profile → DhanHQ Trading APIs →
   generate **Access Token** + copy **Client ID** → put in `.env` (`DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`).
   This is the real unlock — nothing uses live price data until it exists.
2. **(Optional) WhatsApp** — Meta Business account → WhatsApp Cloud API → `WHATSAPP_*` in `.env`.
   Or just use **Telegram** (10-min setup) — already wired.
3. **(Optional) Vercel** — sign up, deploy `web/`, set `NEXT_PUBLIC_API_BASE` to the engine's public URL.
4. **Run the web dashboard** — needs **Node 18+** (not available in the build env):
   `cd web && npm install && npm run dev`.

## 🔜 What I'll do once you hand me the Dhan token
1. **Wire + verify the live feed** — confirm the `dhanhq` SDK method signatures, implement the
   websocket `run()` (currently a guarded `NotImplementedError`), fetch the real instrument master,
   and replace `MockUniverseProvider` with the real NSE universe (+ real turnover for the filter).
2. **Flip sources to live** — `SE_DATA_SOURCE=dhan`, `SE_NEWS_SOURCE=rss`, `SE_CUES_SOURCE=yahoo`.
3. **Start the archives** — let the scheduler accumulate real bars + news (can't be backfilled later).
4. **Begin the validation clock** (below).

## ⏳ Validation gate — before ANY real money (don't skip)
- Forward **paper-trade on real data ≥ 1 month / 20 sessions**; watch the **Strategy Health Score**.
- Re-backtest on the accumulating real archive (synthetic results don't count).
- Trust a strategy only if it survives real out-of-sample data **and** forward paper-trading.
- Expect the example `vwap_ema_adx` strategy + the shadow ML model to need replacing with real
  alpha research — the machinery is proven; a real edge is not.

## Later / scale (when going full-universe live)
- Polars vectorization, real websocket sharding, Redis state store, Postgres (swap from SQLite).
- LightGBM/FinBERT backends (`pip install lightgbm`); retrain the ML scorer on the real corpus.
- Widen the news symbol-alias dictionary from the watchlist to the full universe.

## Reminder
Live order execution is **not implemented** and won't be without an explicit, separate decision —
this is a personal decision-support tool; you place every order.
