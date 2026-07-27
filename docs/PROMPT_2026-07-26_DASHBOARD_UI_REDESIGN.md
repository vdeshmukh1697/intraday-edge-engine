# BUILD PROMPT — Full dashboard UI redesign (senior UI engineer + designer)

> Brief for a fresh Claude Code session. Repo: `/Users/vikrantdeshmukh/Personal projects`
> Frontend lives in `web/`. Live at https://web-beta-beige-60.vercel.app
> Written 2026-07-26.

---

## 0. The ask

Act as a **senior UI engineer and UI designer**. Research how to build the best possible UI for this
product, produce a **design system + per-page redesign plan**, then **implement it across every page**.
This is a full visual and information-architecture overhaul — not a coat of paint.

## 1. What this product actually is (design for THIS, not a generic SaaS dashboard)

An **NSE intraday paper-trading decision-support dashboard**. One user (the owner/operator). It is
consulted in three distinct modes, and the current UI serves only the third well:

| Mode | When | What the user needs in 5 seconds |
|---|---|---|
| **Glance (mobile)** | during market hours, away from the desk | Is the engine alive? What's the book worth? Anything open? Did something just fire? |
| **Review (evening)** | after close | What happened today, why, and what does it mean |
| **Study (desk)** | weekends | Deep tables, charts, research sleeves, track records |

**Honesty is a product requirement here, not a nicety.** This project's whole credibility rests on
never overstating: it is paper money, the strategy has **no demonstrated edge**, the confidence score
is uncalibrated, some movers picks are literally un-buyable. Existing labels you must **preserve and
make MORE prominent, never less**:
- "Paper money" / "no live orders are ever placed" / "decision-support only"
- "Rule score (uncalibrated)" — never call it confidence or win-rate
- "Research only — cannot fill at circuit" vs "Tradeable (ADV ≥ ₹5cr)" on `/movers`
- "survivor panel (optimistic ceiling)", "direction is near-unforecastable — treat leans as weak"
- Shadow/measure-only framing on research sleeves; "Proposals only — nothing applied" on desk output

A redesign that makes losing paper results look like a fintech growth chart is a **failed** redesign.
Good design here means *calibrated* design: the visual weight of a number should match how much we
actually trust it.

## 2. Current state — audit findings (verified, start from these)

**Stack:** Next.js 14 (App Router), React 18, TypeScript, `lightweight-charts` ^4.2. **No Tailwind.**
Styling is one hand-written `app/globals.css` (**751 lines**) using CSS custom properties.

**The two biggest problems, both measured:**
1. **It is effectively not responsive — 751 lines of CSS contain exactly ONE `@media` query.** On a
   phone (the "glance" mode above) tables overflow and cards cram. This is the single highest-value
   fix in the whole project.
2. **Dark-only.** No `prefers-color-scheme`, no light mode. Unusable in bright daylight on a phone.

**Also found:** design tokens exist but are thin (`--bg`, `--bg-elev`, `--border`, `--text`,
`--accent`, `--green`, `--red`, `--amber`, one `--radius`, one `--shadow`) — no type scale, no spacing
scale, no elevation scale, no semantic state tokens, no chart palette. Page components carry layout
concerns inline. 2,490 LOC across 9 pages with visible copy-paste (each page re-implements its own
`pct`/`num` formatters, its own table shell, its own card row).

**Pages (all must be redesigned):**
| Route | File | LOC | Purpose |
|---|---|---|---|
| `/portfolio` | `app/portfolio/page.tsx` | 399 | The ₹1L paper book: value, cash, P&L, equity curve, open positions |
| `/` | `app/page.tsx` | 245 | Leaderboard / today's ranked picks |
| `/watchlist` | `app/watchlist/page.tsx` | 249 | Live prices + sparklines + ●LIVE via `/ws/quotes` |
| `/premarket` | `app/premarket/page.tsx` | 202 | 08:30 briefing: global cues + ranked pre-open watchlist |
| `/paper` | `app/paper/page.tsx` | 464 | Paper-trading account: analytics, per-trade table, by-symbol/strategy/hour breakdowns |
| `/predictions` | `app/predictions/page.tsx` | 239 | Append-only alert log (entry/exit/skip/halt), polls for new rows |
| `/movers` | `app/movers/page.tsx` | 245 | Big-movers research sleeve: tradeable vs research-only, tail splits, track record |
| `/backtest` | `app/backtest/page.tsx` | 208 | Backtest results |
| `/stock/[symbol]` | `app/stock/[symbol]/page.tsx` | 239 | Per-symbol live chart + position detail |

**Components:** `Nav`, `AuthGate` (backend-unreachable banner), `HealthBadge`, `InfoTip` (glossary
tooltips — `web/lib/glossary.ts`), `Sparkline`, `EquityChart`, `CandleChart`, `LiveChart`.
**Data layer:** `web/lib/api.ts` (typed fetchers + `API_BASE` from `NEXT_PUBLIC_API_BASE`, which the
self-healing tunnel script rewrites) — **do not change its response contracts.**

There is also a **new `GET /api/desk`** endpoint (nightly quant-desk review: gap-to-1%/day time
series, hypothesis ledger, proposals) with **no page yet** — see §6 stretch goal.

## 3. Research to do first (do not skip; cite what you take from where)

Study and synthesise, then write down the specific principles you're adopting:
- **Load the `dataviz` skill** before writing any chart/sparkline/meter code — it is the house
  standard for palettes, chart form choice, stat tiles, and light/dark chart legibility.
- **Financial/dense-data UI references:** Bloomberg Terminal (information density, monospace
  alignment, colour used sparingly and semantically), Zerodha Kite & Robinhood (mobile-first Indian/
  retail trading clarity, ₹ formatting, gain/loss treatment), Stripe Dashboard (hierarchy in dense
  tables, empty states), Linear (craft: spacing rhythm, focus states, keyboard affordances).
- **Standards:** WCAG 2.2 AA (contrast 4.5:1 body / 3:1 large + UI boundaries; visible focus;
  target sizes), and the repo's own `~/.claude/skills/coding-guidelines/references/web-frontend.md`
  (**read it — it is binding**: semantic elements, tokens as custom properties, mobile-first
  `min-width` queries, flat specificity, `rem` for text, `prefers-reduced-motion`, one `<h1>`/page).
- **Numeric typography:** tabular/lining numerals (`font-variant-numeric: tabular-nums`) so columns
  of ₹ and % align; right-align numbers; consistent decimal places per column; Indian digit grouping
  (₹1,00,000 not ₹100,000) — the codebase already does this in places, make it universal.
- **Colour semantics for P&L:** green/red must not be the *only* signal (colour-blind safety) — pair
  with sign, arrow, or position. Never use a "success green" for a losing book.

## 4. Deliverable 1 — the design system (do this before touching pages)

Write `docs/UI_DESIGN_SYSTEM.md` and implement it in `app/globals.css`:
- **Token scales** (not one-offs): spacing (4/8-based), type scale with line-heights, radii,
  elevation/shadow, border, motion durations, and a **semantic colour layer** (`--surface`,
  `--surface-raised`, `--text-primary/secondary/tertiary`, `--positive`, `--negative`, `--warning`,
  `--info`, `--neutral`, plus a chart series palette from the `dataviz` skill).
- **Light + dark**, driven by `prefers-color-scheme` with a manual toggle that persists
  (`localStorage`) and does not flash on load. Both themes must pass contrast.
- **A real component layer** — extract the duplication into reusable, typed React components so
  pages stop re-implementing shells. At minimum: `StatTile`, `DataTable` (sticky header, its own
  `overflow-x: auto`, responsive card-stack fallback under ~640px), `Badge`/`Chip`, `Card`,
  `PageHeader`, `EmptyState`, `SkeletonRow`, `TrendCell`, `MoneyCell`, `PctCell`, `Section`.
  Put shared formatters in `web/lib/format.ts` (₹ Indian grouping, %, R-multiple, signed values,
  compact large numbers, relative timestamps IST) — **one implementation, used everywhere.**
- **Mobile-first**: base styles are the 375px layout; enhance upward at ~640/768/1024/1280.
  Tables become stacked cards or horizontally-scrollable panes on small screens — never a squeezed grid.
- Loading, empty, error and **stale-data** states are first-class (this app's data goes stale when the
  market closes or the feed dies — the UI must say which, calmly and unmistakably).

## 5. Deliverable 2 — per-page redesign

For **each** of the 9 pages: state the page's single primary question, redesign the IA so that
question is answered in the first viewport on mobile, then implement. Specifics worth honouring:
- `/portfolio` — the flagship. Book value + today's change is the hero; equity curve second; open
  positions and today's closed trades with their plain-English "why" third.
- `/paper` — dense analytics. Group into: headline scorecard → equity/drawdown → distribution →
  breakdowns (strategy/symbol/hour). The "What the numbers say" narrative block is valuable; keep it
  and make it visually distinct as *interpretation*, not data.
- `/movers` — the tradeable vs research-only split must stay visually unmissable; tail-split
  (`25%↑ / 16%↓`) deserves a proper micro-visualisation rather than plain text.
- `/predictions` — a live log. Needs kind-filtering, readable timestamps, and clear entry/exit/skip
  differentiation; it polls, so new rows should arrive without jarring layout shift.
- `/watchlist`, `/stock/[symbol]` — live/real-time affordances: a genuine "live" indicator tied to
  actual feed freshness, not a decorative pulse.
- `/premarket`, `/backtest`, `/` — bring up to the same system; these are the least loved today.
- **Nav**: 9 routes no longer fit a single desktop row on mobile — design a real responsive nav
  (drawer/bottom-bar on small screens) with the current route unmistakable.

## 6. Stretch (only if the 9 pages are genuinely done and green)
A `/desk` page for `GET /api/desk`: the gap-to-1%/day time series, hypothesis ledger with grades, and
proposals (clearly marked **not applied**). Read `docs/DESK_AGENT.md` first for its honesty contract.

## 7. Hard constraints
- **Do NOT introduce Tailwind or any CSS framework.** Evolve the existing custom-property system.
  A half-migration leaving two styling systems is the worst possible outcome. (Coding-guidelines
  consistency clause: match the codebase; propose framework changes separately.)
- **No new runtime dependencies** without justifying them in the report. `lightweight-charts` stays.
- **Do not change** `web/lib/api.ts` response *contracts*, any Python, the API, or trading logic.
  Adding UI-only fields/helpers is fine; changing what the server returns is not.
- **Preserve every honesty label** (§1). If a redesign makes one less prominent, that's a bug.
- **Accessibility is not optional**: semantic HTML, one `<h1>` per page, labelled controls, visible
  `:focus-visible`, `prefers-reduced-motion`, contrast verified (state the ratios you measured).
- `npx tsc --noEmit` must be clean. Keep components typed; no `any` smuggling.
- Client-side only rendering pattern stays as-is (`"use client"` pages polling the tunnel API).

## 8. Verification (this is the part that proves it)
- **Screenshot every page at 375px AND 1280px, in both light and dark**, using the Browser pane
  (`preview_start` → the Vercel URL or a local `npm run dev`). Paste/report them. A redesign claim
  without screenshots is not accepted.
- Confirm **no horizontal body scroll** at 375px on every page.
- Keyboard-only pass: tab through nav + one dense table; focus must always be visible.
- `npx tsc --noEmit` clean; `npm run build` succeeds.
- Deploy: the tunnel supervisor owns `NEXT_PUBLIC_API_BASE`. Deploy with
  `vercel deploy --prod --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd web` (secrets are in
  `.env`; `NODE_OPTIONS=--dns-result-order=ipv4first` on this network). Then verify the live URL in
  the browser and confirm the honesty labels survived the build.
- **Do not restart the Python launchd services** (`com.vikrant.signal-engine-scheduler`) — this is a
  frontend-only change and the scheduler must keep running Monday's session untouched.

## 9. Report back
Files created/changed; the design-system decisions and the research each came from; before/after
screenshots at both widths in both themes; measured contrast ratios; typecheck/build results; the
deployed URL confirmed working; anything deferred and why.

## 10. Working notes
- Read first: `docs/MORNING_HANDOFF.md` (ops state, don't break it), `web/lib/glossary.ts` (the
  honesty copy lives here), `web/lib/api.ts` (data shapes), `app/globals.css` (what you're replacing).
- The backend must be reachable for real screenshots: check `cat data/tunnel_url.txt` then
  `curl -4 -sS "$(cat data/tunnel_url.txt)/healthz"`. If the tunnel is down the supervisor restarts
  it (`logs/tunnel-sync.log`); an unreachable backend shows the `AuthGate` banner — screenshot pages
  with data, not error states.
- Ship incrementally: design system + shared components first, then page by page, typechecking as
  you go. Do not leave the app half-migrated at any commit boundary.
