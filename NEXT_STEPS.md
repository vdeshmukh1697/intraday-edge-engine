# Next Steps

Status of the path from "built & tested" to "running on real data." See PLAN.md / PROGRESS.md.

## ✅ Done — all integrations live
- **Real news ingestion** — `RSSNewsProvider` (Moneycontrol/ET RSS). Enable: `SE_NEWS_SOURCE=rss`.
- **Real global cues** — `YahooCuesProvider` (yfinance). Enable: `SE_CUES_SOURCE=yahoo`.
- **Free real NSE data** — `YahooNSEBroker` (yfinance, 15-min delayed, no subscription). Enable: `SE_DATA_SOURCE=yahoo_nse`. ✅ NEW
- **Angel One SmartAPI** — free real-time NSE data (needs Angel One account). Enable: `SE_DATA_SOURCE=angelone`. ✅ NEW
- **Dhan adapter** — data-only, unit-tested. Token lives in `.env`. Data API subscription needed for live.
- **Instrument master loaders** — Dhan (`DhanInstrumentMaster`) + Angel One (`AngelOneInstrumentMaster`).
- **Telegram alerts** — free, official Bot API, reliable. Enable: `SE_ALERTER=telegram` (recommended). ✅
  → Setup: @BotFather `/newbot` for a token; `getUpdates` for the chat id. See step 1 below.
- **WhatsApp via CallMeBot** — free but flaky third-party fallback. Enable: `SE_ALERTER=callmebot`.
- **Vercel dashboard** — deployed at https://web-beta-beige-60.vercel.app ✅ NEW
- **Cloudflare tunnel script** — `./run-with-tunnel.sh` starts backend + exposes it publicly + updates Vercel. ✅ NEW
- **Deployment** — `Dockerfile` + `docker-compose.yml` (API + scheduler), persists `data/`.
- **Daily scheduler** — `signal-engine schedule` (pre-market/scan/archive, IST, holiday-aware).
- **Alerting** — WhatsApp (CallMeBot + Meta Cloud) + Telegram + Console alerters.

## 🔜 What you need to do (takes 10–15 minutes total)
1. **Activate Telegram alerts** (2 min — reliable, official, recommended):
   - In Telegram, message **@BotFather** → send `/newbot` → follow prompts → copy the **bot token**
   - Send your new bot any message (e.g. "hi") to open the chat
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy the `"chat":{"id":...}` number
   - Put both in `.env` as `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=`, keep `SE_ALERTER=telegram`
   - _(WhatsApp via CallMeBot is a flaky fallback — it routes through a shared, sometimes-recycled
     number and often doesn't reply. Use Telegram unless you specifically need WhatsApp.)_

2. **Start the system with live backend + dashboard** (1 command):
   ```bash
   ./run-with-tunnel.sh
   ```
   This starts the API, creates a free Cloudflare tunnel, and updates the Vercel dashboard URL automatically.
   Then open https://web-beta-beige-60.vercel.app

3. **Angel One real-time data** (if you want real-time instead of 15-min delayed):
   - Open Angel One demat account (free): angelone.in
   - Create API app → enable TOTP → scan QR with Authenticator
   - Add `ANGELONE_API_KEY`, `ANGELONE_CLIENT_ID`, `ANGELONE_PASSWORD`, `ANGELONE_TOTP_SECRET` to `.env`
   - Set `SE_DATA_SOURCE=angelone`

## Current recommended `.env` for local testing
```
SE_DATA_SOURCE=yahoo_nse      # free, real NSE data, no account
SE_NEWS_SOURCE=rss            # live Moneycontrol/ET headlines
SE_CUES_SOURCE=yahoo          # live global cues (yfinance)
SE_ALERTER=telegram           # reliable, official Bot API
SE_ALLOW_LIVE_ORDERS=false    # always
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=<from getUpdates>
```

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
