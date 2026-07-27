# UI design system — Signal Engine dashboard

> Implemented in `web/app/globals.css` (tokens + layout) and `web/components/ui/*`
> (the component layer). Written 2026-07-26 alongside the full redesign.
> **No CSS framework.** Everything below is plain CSS custom properties.

---

## 0. What this product is, and what that means for the UI

An NSE intraday **paper-trading** decision-support dashboard with one user, consulted in
three modes:

| Mode | When | Must answer in 5 seconds |
|---|---|---|
| **Glance** (phone) | during market hours | Is the engine alive? What is the book worth? Anything open? Did something fire? |
| **Review** (evening) | after close | What happened today, why, what it means |
| **Study** (desk) | weekends | Dense tables, charts, research sleeves, track records |

Two consequences drive every decision here:

1. **Mobile-first is not a nicety.** The previous stylesheet had 751 lines and exactly
   one `@media` query, so the glance mode — the most frequent one — was the worst served.
   Every layout in this system is authored at 375px and *enhanced upward*.
2. **Honesty is a product requirement.** This project's credibility rests on never
   overstating. The design rule that follows: **visual weight must track how much we
   actually trust a number.** A losing paper book must not be able to look like a fintech
   growth chart. Concretely — interpretation is styled as commentary (`Callout`), never as
   data; every uncalibrated figure carries its label in the column header *and* its
   glossary definition; un-fillable picks get their own table with a red badge rather than
   a footnote.

---

## 1. Research adopted, and where each rule came from

| Source | What was taken |
|---|---|
| **`dataviz` skill** (house standard) | The whole colour method: form chosen before colour; the eight-slot categorical palette and its light/dark steps; the status palette; "sequential = one hue, diverging = two hues + neutral midpoint"; thin marks, 4px rounded data-ends, 2px gaps between fills; a stat tile instead of a one-bar chart; hero figure ≥48px; **run the validator, don't eyeball it**. |
| **`coding-guidelines/references/web-frontend.md`** (binding) | Semantic elements; one `<h1>` per page; every input has a real `<label>`; tokens as custom properties; **mobile-first `min-width` queries**; flat single-class specificity; `rem` for text; `prefers-reduced-motion`; visible `:focus-visible`; wide content scrolls inside its own container, never the body. |
| **WCAG 2.2 AA** | 4.5:1 body text, 3:1 large text and UI component boundaries, visible focus, ≥44px touch targets (all controls are `min-height: 2.75rem` on phones), `aria-current` for the active route, `aria-sort` on sortable headers. |
| **Bloomberg Terminal** | Information density is a feature for the study mode; colour used *sparingly and semantically* (only P&L, direction and status are ever coloured); monospace + tabular numerals so columns align. |
| **Zerodha Kite / Robinhood** | Indian digit grouping (₹1,00,000, ₹1.25L, ₹2.5Cr — never ₹100,000); the book value as a hero figure with today's delta directly beneath; gain/loss as sign + arrow + colour, in that order of importance. |
| **Stripe Dashboard** | Hierarchy inside dense tables (quiet uppercase headers, one emphasised primary column); first-class empty states that say *what would put data here*. |
| **Linear** | Spacing rhythm from a single 4/8 scale; restrained elevation; keyboard affordances that are always visible. |

**Numeric typography.** `font-variant-numeric: tabular-nums` on every numeric column and
on the mono class, so ₹ and % columns align vertically. Proportional figures on hero and
stat-tile values (per the dataviz skill — tabular is for columns that must line up).
Numbers right-aligned; fixed decimals per column kind (price 2dp, whole ₹ 0dp, % 2dp,
probabilities 0dp).

**Colour semantics for P&L.** Green/red is never the only signal. Every signed figure is
rendered through `Delta`/`Money kind="signed"`/`pctSigned`, all of which emit an explicit
`+`/`−` and (for headline figures) a `▲`/`▼` glyph. Direction badges carry the word LONG or
SHORT. Status badges carry the status word. Zero is neutral, never green.

---

## 2. Token scales

All tokens live on `:root` in `web/app/globals.css`.

### Space — 4px base, 8px rhythm
`--space-1 .25rem · --space-2 .5rem · --space-3 .75rem · --space-4 1rem · --space-5 1.5rem
· --space-6 2rem · --space-7 3rem`

### Type — `rem` throughout so OS text scaling works
| Token | Size | Used for |
|---|---|---|
| `--text-2xs` | 11px | column labels, chips, badges |
| `--text-xs` | 12px | captions, sub-values, notes |
| `--text-sm` | 13px | table body, dense UI |
| `--text-base` | 14px | body |
| `--text-md` | 16px | lead paragraphs, card titles |
| `--text-lg` | 18px | `h3` |
| `--text-xl` | 21px | `h2` |
| `--text-2xl` | 24px | `h1` |
| `--text-stat` | 24px | stat-tile value |
| `--text-hero` | `clamp(1.875rem, 9vw, 2.75rem)` | the one number a page leads with |

Line heights: `--leading-tight 1.2` (headings), `--leading-snug 1.35`,
`--leading-normal 1.55` (body).

### Radii, elevation, motion
`--radius-sm 4px · --radius 8px · --radius-lg 12px · --radius-pill 999px`
`--shadow-1/2/3` (a hairline border does most of the separating; shadow is a hint)
`--dur-fast 110ms · --dur 180ms · --ease cubic-bezier(.2,0,.2,1)` — all of it inside
`prefers-reduced-motion: reduce`.

### Breakpoints (mobile-first, `min-width` only)
`40rem` (640px — tables stop stacking) · `48rem` (768px — hero splits, wider padding) ·
`64rem` (1024px — nav becomes an inline row, two-column sections).

---

## 3. Colour — semantic layer, both themes selected

Light is the base. Dark is applied by
`@media (prefers-color-scheme: dark) :root:where(:not([data-theme="light"]))` and then by
`:root[data-theme="dark"]`, declared *after* the media block so a manual stamp wins in
both directions. The stored choice is applied by an inline script in `<head>` before first
paint, so the page never flashes the wrong theme.

### Measured contrast (WCAG 2.2, computed — not eyeballed)

| Pair | Light | Dark |
|---|---|---|
| body primary on card surface | **19.17:1** | **17.42:1** |
| body primary on page | 17.86:1 | 19.44:1 |
| secondary text on surface | **8.51:1** | **9.72:1** |
| tertiary text on surface (smallest text on the page) | **5.43:1** | **6.02:1** |
| tertiary text on sunken (table header strip) | 4.76:1 | 6.43:1 |
| link / accent on surface | **5.79:1** | **6.40:1** |
| positive (gain) on surface | 7.35:1 | 7.46:1 |
| negative (loss) on surface | 6.08:1 | 6.44:1 |
| warning ink on surface | 7.08:1 | 9.30:1 |
| control border `--border-control` on surface (UI 3:1) | **3.65:1** | **3.27:1** |
| control border on sunken | 3.20:1 | 3.49:1 |
| focus ring on surface / on page | 5.79 / 5.39:1 | 6.40 / 7.14:1 |
| text on accent button | 5.94:1 | 7.14:1 |
| badge inks on their tints (pos/neg/warn) | 6.42 / 5.33 / 6.44:1 | 6.90 / 6.30 / 8.46:1 |

Everything that carries meaning clears AA. `--border` (the decorative hairline between
table rows) is deliberately below 3:1 — WCAG 1.4.11 exempts purely decorative separators;
anything a user must *perceive as a control boundary* uses `--border-control`, which is
measured above.

### Chart palette
The dataviz reference palette, unmodified, validated for these surfaces:

```
node scripts/validate_palette.js "<8 light hexes>" --mode light --surface "#fcfcfb"  → ALL CHECKS PASS
node scripts/validate_palette.js "<8 dark hexes>"  --mode dark  --surface "#1a1a19"  → ALL CHECKS PASS
```

Light run reports a contrast WARN on aqua/yellow/magenta (slots 3–5) — the documented
**relief rule** applies, and it is satisfied here because every chart in this app carries
visible direct labels or a table view. In practice the dashboard uses at most two series
at once, so slots 1–3 (which validate all-pairs in both modes) are the only ones in play.

Charts are configured in JavaScript (`lightweight-charts` cannot read CSS custom
properties), so `web/lib/theme.ts` reads the tokens off the document and re-applies them on
both the OS media-query change and the in-app toggle event.

---

## 4. Component layer

`web/components/ui/`

| Component | Purpose |
|---|---|
| `PageHeader` | The single `<h1>`, an eyebrow, the honesty lede, an optional status badge. |
| `Section` | A titled `<section>` with a real `h2`/`h3`, a `note` line for scope/caveats, and an `aside` slot. |
| `Card` | A bare surface, for hero blocks. |
| `Callout` | **Interpretation and honesty copy — never data.** Tones: neutral / info / warning / danger. Visually distinct from every data surface by design. |
| `EmptyState` | Says what would put data here, not just "no data". |
| `LoadingBlock` / `SkeletonRow` | Reserve layout instead of collapsing it; `role="status"`. |
| `Badge` / `Chip` / `ChipRow` | Tagged state. Always carries a word, never colour alone. |
| `StatTile` / `Hero` | A single current value (+ delta + context). Never a one-bar chart. |
| `Meter` | One ratio against a limit, with `role="meter"`. |
| `BarRows` | Signed horizontal magnitude bars; label and value always visible. |
| `Histogram` | Per-trade P&L distribution, sign-split about zero, 2px gaps, per-bar tooltip. |
| `TailSplitBar` | The movers tail split as two arms diverging from a neutral centre; percentages and ↑/↓ stay on screen. |
| `LineChart` / `AreaChart` | Dependency-free SVG with a crosshair + value readout on hover and an `aria-label` summary. |
| `DataTable` | The one table implementation (below). |
| `cells.tsx` | `Money`, `Pct`, `Delta`, `SymbolLink`, `DirectionTag` — the shared value renderers. |

`web/lib/format.ts` is the **single** implementation of every number and date format:
`inr`, `inrPrice`, `inrSigned`, `inrCompact` (lakh/crore), `pct`, `pctSigned`, `probPct`,
`num`, `int`, `rMultiple`, `conf`, `signed`, `signCls`, `signArrow`, `parseTs`, `hhmm`,
`hhmmss`, `dayTime`, `isoDay`, `dayLabel`, `ago`, `clock`, `countCompact`. Pages no longer
define their own. Note `countCompact` (share counts: "3.45L") is separate from `inrCompact`
(money: "₹3.45L") — a volume must never render with a ₹.

### DataTable — the responsive contract

One DOM, two layouts, expressed mobile-first:

- **Base (< 40rem):** `thead` is visually hidden; each `<tr>` becomes a labelled card; each
  `<td>` renders `attr(data-label)` beside its value. Columns opt out with `hideOnStack`,
  become the card title with `primary`, or go full-width with `spanOnStack`.
- **From 40rem:** the real table returns via `display: table-row/table-cell`; labels are
  dropped; numeric columns right-align again.
- Wide research tables can set `mobile="scroll"` and scroll inside `.dt-scroll`
  (`overflow-x: auto`) instead. **The body never scrolls horizontally.**
- `tall` caps the height and scrolls vertically, which is what makes the sticky
  `thead` earn its keep.
- Sortable columns render a real `<button>` in the header with `aria-sort` on the `<th>`.
- `subRow` renders the plain-English "Why: …" line under a trade, merged into the card
  above it on phones.
- A cell whose renderer returns `null` is **dropped** from the card stack (a selective
  strategy leaves most columns empty on most rows) and shows an explicit `—` once the
  real table returns.
- The `primary` cell usually carries a summary value for the card view that duplicates a
  `hideOnStack` column. Wrap that duplicate in `.stack-only` so it disappears at 40rem.

The stylesheet went from **1 `@media` query to 16** — all width queries are `min-width`,
at 40 / 48 / 64rem, plus `prefers-color-scheme` and `prefers-reduced-motion`.

---

## 5. State design

Loading, empty, error and **stale** are first-class, because this app's data legitimately
goes stale every day at 15:30.

- **Loading** → `LoadingBlock` skeletons that hold the layout.
- **Empty** → `EmptyState` naming the thing that would fill it.
- **Error** → `ErrorBanner` with `role="alert"`; a *transient* poll failure never blanks a
  page that already has good data on screen.
- **Stale** → `FeedStatus` / the watchlist quote strip distinguish three states with
  different words: **live** (dot pulses, "last bar 4s ago"), **stale** ("no bar for 3m —
  market closed, or the feed dropped"), **offline** ("no live session running"). The pulse
  is tied to real freshness — on the watchlist, to the timestamp of the last received tick,
  not merely to the socket being open.

---

## 6. Navigation

Eight routes never fitted a phone-width row. One route list, two layouts:

- **< 64rem:** a disclosure drawer. The bar shows the brand, the *current route name*, the
  theme control and a 44px menu button with `aria-expanded`/`aria-controls`. The drawer
  closes on navigation.
- **≥ 64rem:** the same `<ul>` becomes an inline row inside the bar via flex `order`.

The current route carries `aria-current="page"` — announced, not just drawn (an inset rule
plus tinted background on mobile; an underline on desktop).

The brand block permanently reads **“Paper money · no live orders”** in the warning ink, so
the most important honesty label is on every page, above the fold, in both themes.

---

## 7. Honesty labels — where each one lives now

| Label | Where |
|---|---|
| Paper money / no live orders | Nav brand (every page), footer, `/portfolio` + `/paper` header badge and lede |
| "Rule score (uncalibrated)" — never confidence/win-rate | Column header on `/`, `/premarket`, `/paper`, `/predictions`, `/stock`; glossary tooltip; a dedicated `Callout` on `/` |
| Tradeable (ADV ≥ ₹5cr) vs Research only — cannot fill at circuit | `/movers`: **two separate tables**, each with its own tone badge in the section title |
| Survivor panel (optimistic ceiling) | `/movers` top callout, first bullet |
| Direction is near-unforecastable — treat leans as weak | `/movers` top callout, second bullet; every lean badge reads "weak lean …" |
| Shadow / measure-only | `/movers` header badge, sleeve P&L tile sub ("never placed") |
| Modelled pre-ledger ₹ figures | Warning badge on the affected rows, `/portfolio` and `/paper` |
| Backtests are an upper bound | `/backtest` lede + closing callout pointing at the live paper record |
| Interpretation ≠ evidence | `/paper` "What the numbers say" is a `Callout`, with an explicit "an interpretation, not extra evidence" line |

---

## 8. Conventions to keep

- New colour? Add a **semantic** token, never a raw hex in a component.
- New table? Use `DataTable`. New number? Use `lib/format.ts`.
- New chart? Load the `dataviz` skill first, pick the form before the colour, and run
  `scripts/validate_palette.js` before shipping a categorical palette.
- Any control smaller than `2.75rem` tall on phones is a bug.
- Any number that is not measured must say so, in the label — not in a tooltip alone.
