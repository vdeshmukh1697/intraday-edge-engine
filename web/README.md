# Signal Engine Dashboard (Phase-6 frontend)

A read-only Next.js 14 dashboard for the intraday **Signal Engine**. It visualizes
the engine's leaderboard, pre-market outlook, backtest performance, and per-symbol
intraday charts.

> **Decision-support only. Not investment advice. No live orders. Intraday trading
> carries substantial risk of loss.** This UI never places trades — it only reads
> from the engine API.

## Stack

- Next.js 14 (app router) + React 18 + TypeScript
- [`lightweight-charts`](https://github.com/tradingview/lightweight-charts) v4 (TradingView) for candlesticks and the equity curve
- Plain global CSS dark theme (no Tailwind)

## Prerequisites

The Python FastAPI engine must be running and reachable. Start it with one of:

```bash
signal-engine serve
# or
./run.sh serve
```

By default it listens on `http://127.0.0.1:8000`.

## Setup

> There is **no** Node/npm in the authoring environment, so this was written but not
> built here. Run these on a machine with Node 18+ installed.

```bash
cd web
cp .env.local.example .env.local   # then edit values
npm install
npm run dev                         # http://localhost:3000
```

## Environment variables

Set in `.env.local` (copy from `.env.local.example`):

| Variable               | Default                 | Purpose                                                              |
| ---------------------- | ----------------------- | -------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_BASE` | `http://127.0.0.1:8000` | Base URL of the engine API (no trailing slash).                      |
| `NEXT_PUBLIC_API_KEY`  | _(empty)_               | Optional. When set, sent as the `X-API-Key` header on every request. |

Both are `NEXT_PUBLIC_*` so they are inlined into the client bundle — these are read
by the browser, not the Next server.

## Pages

| Route             | Description                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `/`               | **Leaderboard** (hero): ranked signals with date / universe / top / news-veto / ML-shadow controls, scan stats, and a Strategy Health badge. |
| `/premarket`      | Index outlook card (gap bias, expected gap %, risk tone, drivers) + ranked pre-open picks.                               |
| `/backtest`       | Metrics cards, an equity-curve chart, and the strategy-health component breakdown.                                       |
| `/stock/[symbol]` | Candlestick chart with VWAP / EMA-fast / EMA-slow overlays and a **Go live** button that streams bars over WebSocket.    |

Symbols in the leaderboard and pre-market tables link to `/stock/[symbol]`.

## API contracts consumed

- `GET /api/leaderboard?date&universe&top&news&ml`
- `GET /api/premarket?date`
- `GET /api/backtest?start&days`
- `GET /api/chart/{symbol}?date`
- `WS  /ws/chart/{symbol}?date_str&speed`

All typed in [`lib/api.ts`](./lib/api.ts).

### Notes on the ML-shadow column

When the **ML shadow** toggle is on, the table shows an extra **ML conf** column.
Caption: _"conf = rules; ML conf = shadow model, does not change ranking."_

### Live streaming

The chart's **Go live** button opens the WebSocket and appends each incoming bar via
`series.update(bar)` — an incremental update, so the chart is not redrawn on each tick.
A terminal `{done:true}` message closes the stream. Because browsers cannot set custom
headers on a WebSocket handshake, the API key (if configured) is also appended as an
`api_key` query parameter; this is harmless if the engine ignores it.

## Vercel deploy notes

1. Import the repo into Vercel and set the project **root directory** to `web/`.
2. The engine must be reachable from the public internet over **HTTPS** (a browser on
   an HTTPS page cannot call an `http://127.0.0.1` API). Expose it with e.g. a
   [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
   and set:
   - `NEXT_PUBLIC_API_BASE` = the engine's public HTTPS URL (e.g. `https://signals.example.com`).
     The dashboard derives the WS URL by swapping `http→ws`, so an `https` base yields a
     secure `wss://` socket automatically.
   - `NEXT_PUBLIC_API_KEY` = the same value as the engine's `SE_API_TOKEN`.
3. Redeploy after changing env vars (they are inlined at build time).

## Project layout

```
web/
├── app/
│   ├── layout.tsx              # shell: nav + disclaimer footer
│   ├── globals.css             # dark theme
│   ├── page.tsx                # / leaderboard
│   ├── premarket/page.tsx
│   ├── backtest/page.tsx
│   └── stock/[symbol]/page.tsx
├── components/
│   ├── Nav.tsx
│   ├── HealthBadge.tsx
│   ├── CandleChart.tsx         # lightweight-charts, client-only
│   └── EquityChart.tsx
├── lib/
│   ├── api.ts                  # typed fetch + WS helpers
│   └── format.ts
├── package.json
├── tsconfig.json
├── next.config.js
├── .env.local.example
└── .gitignore
```
