# TradingAgents (TauricResearch) — Due-Diligence Report

**Date:** 2026-07-07
**Subject:** [github.com/TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) v0.3.1 (commit 01477f9, 2026-07-05), paper arXiv:2412.20138, tauric.ai
**Method:** full clone read line-by-line by 5 module-cluster auditors (79 source files, ~16.2k LOC incl. tests), one auditor inventorying our own repo to block duplicate recommendations, one external-evidence agent (replications, GitHub issues, follow-up literature), then an adversarial verification pass over every proposed borrow. All claims below cite files.

---

## 1. Executive verdict

**TradingAgents is the mirror image of our project.** They built sophisticated LLM orchestration with **no evaluation discipline** — no cost model in the headline results, no debate-vs-no-debate ablation anywhere in the repo, and a backtest that independent replicators showed was contaminated by look-ahead leakage. We built rigorous evaluation discipline (edge_verdict gates: n≥2000, OOS headline, PBO<0.10, Deflated Sharpe; real Indian cost model; honest no-edge verdict) with **no LLM anywhere** in the pipeline. Their alpha claims are refuted — but that was never where their value was.

**Three things are genuinely worth taking** (after an adversarial verification pass that killed or reshaped 6 of 16 reader-proposed borrows — details in §5):
1. **The decision→outcome grading loop, in deterministic form.** Their reflection mechanism (`graph/reflection.py` + `agents/utils/memory.py`) closes a loop we already have all the data for: our ₹1L paper book logs every decision with reasons and every realized ₹-outcome with real charges, but nothing grades closed trades against the index. The verified core is **alpha-vs-NIFTY tagging on every closed trade** — no LLM required; their optional LLM "lesson" call can come later, their markdown storage should not come at all (§5, S1).
2. **Ops/data hardening redirected at our actual holes.** The verifiers found the readers' generic recommendations mostly duplicated things we'd built — but chasing them surfaced real gaps: `factory.py` silently falls back on a typo'd `SE_ALERTER`/`SE_DATA_SOURCE`, research parquet caches have no config-signature invalidation, secondary fetchers lack a typed error taxonomy, and EOD/research frames lack the staleness assert our live feed has. ~2 days total, all deterministic.
3. **A recorded set of design conventions for the future LLM/FinBERT news-tone analyst** (EDGE_ROADMAP #4/#9): pre-fetch-then-single-structured-call (their fabrication bug #984 is the free lesson), quick/deep dual-model split, snapshot-grounded numbers, structured-output-with-fallback. **Zero days now** — there is no LLM call site anywhere in `signal_engine/` to apply them to — but written down, they make us arrive at that build with their scar tissue pre-installed.

**What we must not take:** the multi-agent debate topology itself. All five code auditors independently reached the same conclusion — it is ~10–13 LLM calls per ticker per day whose judges never see the underlying evidence (they grade debate rhetoric, not analyst reports), whose "risk" personas are assigned their conclusions in the prompt, and whose value is measured nowhere in the repo. For a system whose documented leak is ~8bps execution cost and absent informational edge, buying LLM deliberation theater is spend in exactly the wrong place.

**Validation of our architecture:** the ecosystem scan found **no credible intraday adaptation** of TradingAgents anywhere — the framework is widely noted as too slow and too costly for anything faster than daily/swing decisions. Our deterministic sub-minute pipeline is not behind the state of the art here; it is the only viable shape for our horizon.

---

## 2. What TradingAgents actually is (code, not README)

A LangGraph pipeline, strictly sequential, one ticker one date per run:

```
[Market | Sentiment | News | Fundamentals analysts]   ← each: ReAct tool loop, then Msg-Clear
        → Bull ⇄ Bear researcher debate (fixed round budget, not convergence)
        → Research Manager (deep model, judges the debate TRANSCRIPT — never sees the reports)
        → Trader (quick model)
        → Aggressive → Conservative → Neutral risk round-robin
        → Portfolio Manager (deep model, gets memory-log lessons injected)
        → deterministic regex parse of the 5-tier rating   (they DELETED a second LLM call here)
```

Key mechanisms as implemented:

- **State propagation** (`graph/setup.py`, `agents/utils/agent_states.py`): each analyst writes a distilled `*_report` string into typed state; a dedicated `Msg Clear` node deletes the raw tool transcript before the next stage. Downstream prompt sizes stay flat regardless of tool round-trips. Deliberate token hygiene — one of their better ideas.
- **Debate termination** (`graph/conditional_logic.py`): purely count-based (`count >= 2 * max_debate_rounds`), speaker alternation by `current_response.startswith("Bull")` string matching — fragile enough that they shipped a crash-class fix (#1088, full path maps) to paper over router fall-throughs.
- **Reflection loop** (`graph/trading_graph.py`, `graph/reflection.py`, `agents/utils/memory.py`): the standout. Phase A: at run end, append `[date | ticker | rating | pending]` + decision text to an append-only markdown log — **zero LLM cost**. Phase B: at the *next* run for that ticker, fetch realized 5-day close-to-close return, compute **alpha vs a benchmark resolved by exchange suffix** (their map already has `.NS → ^NSEI`), make exactly **one cheap LLM call per resolved entry** producing a 2–4 sentence lesson under a fixed rubric, then atomically rewrite the log (temp file + `os.replace`, rotation never prunes pending entries). The last 5 same-ticker entries + 3 cross-ticker lessons inject into the PM prompt. No ChromaDB, no embeddings — a flat greppable markdown file. README's memory claims are fully backed by this code.
- **Checkpoint/resume** (`graph/checkpointer.py`): per-ticker SQLite savers; thread ID = `sha256(TICKER:date:config_signature)` so resuming under a changed graph shape starts fresh instead of silently replaying a stale run (#1089).
- **Data layer** (`dataflows/`): vendor-routing facade with a **three-type error taxonomy** (sized to exactly the three router reactions: try-next-vendor / fail-this-category / degrade-with-sentinel), stale-OHLCV hard guard, look-ahead-safe news windows, and no-fabrication sentinel strings returned *as the tool result* ("Do not estimate or fabricate values — report that data is unavailable").
- **LLM layer** (`llm_clients/`, 12 files): quick-think/deep-think dual-model split — 9 of 11 roles run on the cheap model; the frontier model touches only the two judgment nodes. Declarative capability/quirk tables per model family. A thread-locked token/call counter (`cli/stats_handler.py`) that stops at ephemeral display.

**Code quality, honestly assessed:** better than the marketing suggests. Small single-purpose modules, issue-number-annotated comments matching real regression tests, checkpoint tests that actually crash and resume a real SQLite-backed graph, network tests that mock the wire but not the logic under test. This is a maintained project (v0.3.1 two days before this report), not abandonware. The weak spots: error-prose-returned-as-successful-tool-result in `y_finance.py` fundamentals paths (contradicts their own sentinel design), a daily-rolling OHLCV cache key that re-downloads 5y of data per symbol per day, and prompt/context mismatches (trader told to "anchor in the analysts' reports" that are not in its prompt).

---

## 3. Claim autopsy — does it make money?

The paper claims Sharpe 5.60–8.21, max drawdown <2.2%, on 3 megacap US tickers. **No independent evidence supports this; substantial evidence refutes it.**

| Evidence | Finding | Severity |
|---|---|---|
| GitHub issue #203 + #168 logs | **Confirmed look-ahead bias in the released code**: analysts fetched *current* news/prices during historical backtests; a replicator found 2025-dated news links in logs of a Jan–Mar 2024 backtest. Fixed only Mar 2026 (commit e111388) — long after publication. | **Fatal** |
| "Profit Mirage" (arXiv:2510.07920) | **Training-data contamination measured**: 55.68% Sharpe decay and 50.18% return decay when re-evaluated strictly after the LLM's knowledge cutoff; ~69% of predictions unchanged under counterfactual input perturbation → memorization, not analysis. | **Fatal** |
| Issue #168 replication (AAPL, same window, recommended models) | **−25.4% return, Sharpe −12.3, 37.7% win rate** vs the paper's ~+26%. Maintainer's official response concedes results "aren't guaranteed to match any published figure" and reframes the repo as a "research scaffold". README now carries a reproducibility disclaimer (commit 8a22594). | **Fatal** |
| funrobot0804, 100 repeated runs, identical inputs | Near-uniform buy/sell/hold distribution across runs → single-run backtests (including the paper's) carry ~no evidential weight. | Serious |
| FINSABER (KDD 2026) | Singles out the "3 months / 3 symbols" evaluation as exemplifying survivorship, look-ahead, and data-snooping bias; LLM timing strategies underperform passive benchmarks over 20y/100+ symbols. | Serious |
| Issue #168 cost accounting | $1,838 commissions on a $10k account over 53 trades in 3 months (~18% drag) once realistic costs are added; $0.12–$5 LLM cost per decision on top. Paper's headline numbers model **no transaction costs** — the exact failure our engine's edge-after-cost gate exists to prevent. | Serious |

**Context:** ~91.6k stars, #1 on GitHub Trending — and three Hacker News submissions that each got 1–2 points and zero comments. The star count reflects the pitch, not validated performance. The most-commented issues include "#225: Has anyone actually made money with this project?". The biggest ecosystem story is a Chinese A-share localization fork (TradingAgents-CN, ~29.9k stars); **nobody has shipped a credible India fork or any intraday adaptation.**

**Bottom line:** treat TradingAgents as a well-engineered LLM-orchestration codebase attached to a refuted performance claim. Mine the engineering; ignore the alpha.

---

## 4. Head-to-head

| Dimension | TradingAgents | Us (intraday-edge-engine) |
|---|---|---|
| Horizon | Daily, one decision/ticker/day; too slow+costly for intraday (ecosystem consensus) | 1-min bars live intraday + swing research |
| Market | US megacaps (yfinance/AlphaVantage); `.NS` resolution exists but untested for our needs | NSE native: paid Dhan WS feed, real Indian charges itemized |
| Cost modeling | **None in headline results** | Edge-after-cost rejection gate on every plan; measured ~8.2bps live leak |
| Evaluation | No ablations, no multiple-testing control; leaked backtest | Walk-forward + PBO, Deflated Sharpe, HLZ correction, causality proofs |
| News/sentiment | LLM analysts over Reddit/StockTwits/news APIs | Deterministic RSS + lexicon + event classes + overlay gating (LLM/FinBERT = roadmap gap) |
| Decision→outcome learning | **Reflection loop w/ alpha-graded lessons** ← their best piece | Predictions + ledger fully logged, ML retrain loop — but lesson synthesis is manual |
| Risk gating | LLM "portfolio manager" opinion, no hard constraints | Deterministic: ATR stops, R:R floor, drawdown breaker, caps, no-leverage sizing |
| LLM usage | 11 roles, 10–13 calls/ticker/day | Zero (by design so far) |

We are not behind them. On every dimension that determines whether numbers can be trusted, we are ahead. They are ahead on exactly one loop (reflection) and on accumulated LLM-I/O scar tissue.

---

## 5. The steal list — after adversarial verification

> Every reader-proposed borrow went through an independent adversarial verifier (default stance: refute) that re-read the cited TradingAgents code AND our code. Of 16 candidates: **8 survived as "adapt" (usually reshaped and smaller), 6 were killed, 2 were redirected at bugs in our own code the readers hadn't seen.** Ideas below carry the verifier's re-estimated effort, not the reader's.

### Tier 1 — verified, do these (~2–3 days total)

**S1. Alpha-graded decision loop on the ₹1L paper book** — *adapt, ~1–1.5 days core (deterministic), +1 day optional LLM layer later*
The one borrow that changes what the system *does*. Verifier's reshaping of their reflection loop (`reflection.py`, `memory.py`, `trading_graph.py:_resolve_pending_entries`):
- **The valuable core needs no LLM at all.** Our `paper_trades` already links entry→exit with net-of-charges P&L (`pnl_pct_net`, `r_multiple`, `won`) — but `analytics/paper.py` grades `win = net P&L > 0` with **zero benchmark adjustment**. On a long-only book that conflates stock-picking with the index tide. Verified gap. Build: tag every closed trade with **alpha vs ^NSEI over the holding period** at ledger exit (T+n resolver for swing), carry the *stated entry reasons* onto the outcome record, surface an alpha column in `/portfolio` + the weekly analysis.
- **Grade from our ledger fills net of real charges — never yfinance closes.** Their `_fetch_returns` uses cost-free third-party closes; that reintroduces measurement error at exactly the ~8bps scale our research identified as the leak.
- **Do NOT port their markdown journal** (see kill list) — this lands as a column/table in our existing SQLite (predictions/trade IDs as keys), ~0.5 day inside the ledger we already have.
- The one-cheap-LLM-call-per-resolved-trade "lesson" layer (their reflection prompt is genuinely well-engineered — appendix A) is optional and comes only after the deterministic core proves useful. **Hard caveat:** our 3-session live analysis found no at-entry feature separating winners from losers; lessons are decision-support narrative and must never gate trades until they pass `research/` gates.

**S2. Typed error taxonomy for secondary fetchers — taxonomy yes, router no** — *adapt, ~1 day*
Verifier narrowed the reader's claim: we have **no fallback chains** (`factory.py` selects exactly one source per role), so their explicit-chain router would route a chain of one — skip it. What survives: `signal_engine/data/errors.py` with `FetchError` base + `NoDataError` (carrying staleness/symbol detail), `RateLimitedError`, `NotConfiguredError`; generalize `brokers/dhan.py`'s existing `DhanRateLimitError`/`DhanDataNotSubscribedError` into subclasses (back-compat preserved — same trick they used making NotConfigured also a ValueError); retrofit the `yahoo_cues`/backfill/`events_dataset` callers. Two norms come with it: never return error prose as data (their documented anti-pattern), and one place declaring which categories may degrade (news) vs must fail loudly (feed, ledger).

**S3. Fail-loud config — redirected at OUR actual bug** — *adapt, ~0.25 day*
Verifier refuted the reader's framing (our YAML config is already fail-loud via pydantic) but confirmed the failure *mode* lives elsewhere in our repo: **`factory.py`'s else-branch fallbacks silently swallow a typo'd `SE_ALERTER` or `SE_DATA_SOURCE`** — a misspelled env var at 08:30 quietly runs the wrong alerter/data source all session on an unattended launchd machine. Fix: unknown selector values raise at startup. This is the redirected version of their `_ENV_OVERRIDES` fail-loud philosophy (`default_config.py`).

**S4. Staleness guard — residual only** — *adapt, ~0.5 day*
~70% already exists (`obs/freshness.py` + runner entry-suppression — we built it after our own frozen-websocket incident). Verified residual: (a) Telegram escalation when live staleness trips (today it suppresses silently), (b) recency assert + skip-with-reason on EOD/research frames (`scan/real_harness.py`, research frame builders) so indicators are never computed on a stale Yahoo/bhavcopy frame.

**S5. Config-signature cache keys for research parquets** — *adapt, ~0.25 day*
Verifier cut the reader's broad claim (premarket/ML have no cached state; live resume replays raw bars, structurally immune) down to the real instance: **`research/long_panel.py` and `research/events_dataset.py` parquet caches have no config-signature invalidation** — change universe/embargo/label params and a stale panel silently serves. Fix: ~30-line params-sidecar JSON (universe hash, START, label/embargo params) written next to each parquet, compared on load, rebuild on mismatch; plus a feature-schema hash inside the model payload. Their `checkpointer.py:thread_id` sha256-of-config is the donor pattern.

### Tier 2 — record as design conventions, zero days now

These verified as *correct engineering with no current call site* — `signal_engine/` contains zero LLM calls, and the funded next sentiment step (FinBERT, roadmap #4/#9) is a **local classifier with no prose output to parse**. Writing them into EDGE_ROADMAP as constraints on any future LLM-tone-analyst build costs ~0.1 day and buys their scar tissue for free:

- **Pre-fetch everything, one structured LLM call, no tool loop** — their live-verified fabrication bug (#984: prompt demanded sources the agent had no tool for → LLM fabricated Reddit/StockTwits content) is precisely the failure mode an LLM news-tone analyst risks. Build shape: behind our existing `SentimentModel` interface, default OFF, A/B via `scan --no-news`. (~1–2 days *when built*.)
- **Quick/deep dual-model split + ₹-denominated cost meter from call one** — their ratio (cheap model for 10 of 12 roles; frontier only where judgment concentrates) is the lesson; an LLM analyst that can't pay its own API bill in signal quality fails our edge-after-cost philosophy applied to compute. (~0.5–1 day *when built*.)
- **Snapshot-grounded numbers + instrument-identity anchor in any LLM prompt** — verifier correction: their "verified snapshot" is an *optional tool call*, not deterministic injection; ours must be injected deterministically. Identity anchor addresses their #814 (LLM substituted a different company mid-run) — NSE's ambiguous symbols (IDEA, Jindal-family) make it worse for us.
- **Constrain output upstream, parse deterministically downstream** — they deleted a second LLM call by forcing structured output + regex parse; baseline practice we should simply follow.

**Also worth 0.25 day whenever CI is next touched:** their clean-install smoke job (fresh `pip install .` + bare import) — caught an undeclared-dep breakage (#994) that dev-extras test runs can't see. *(Reader-proposed; below the adversarial pass's cut line, but trivially self-evident.)*

### Killed by the adversarial pass (readers proposed, verifiers refuted)

| Proposed borrow | Why it died |
|---|---|
| Append-only **markdown decision journal** | Its two selling points are false for us: appends are **not** atomic (only the batch rewrite is), and idempotency fails exactly under our warm-start replay → duplicate entries. We're not DB-less; SQLite `open_positions`→`paper_trades` already IS the pending/resolved lifecycle, crash-safe via WAL + `rebuild()`. Also PEP 604 syntax breaks our Python 3.9. |
| **Verified-snapshot grounding as a build item** | Solves a problem we structurally don't have: every number in every Telegram alert comes from `alerts/plain.py` stdlib templates fed by code-computed values. And their implementation doesn't even guarantee it (optional tool call). Survives only as a convention (Tier 2). |
| **Undated-content news exclusion** | Our `news/rss.py:139-142` already drops undated entries unconditionally — **stricter than their rule** (they keep undated items in live windows). And our event studies never touch RSS news. Nothing to build. |
| **Structured-output-with-fallback wrapper** | Zero LLM call sites to harden; FinBERT returns softmax probs, no JSON to parse. LangChain-coupled + PEP 604. Convention only. |
| **₹ LLM cost ledger as a build item** | Accounting for spend that doesn't exist; even roadmap #4/#9 in stated form (local FinBERT) is token-free. Self-write in ~0.5 day if a paid call ever lands. |
| **Broad "constrain-upstream" / dual-model / env-table adoptions** | Each verified as real in their code but "architecture tourism" here — no decision we make today changes. Reduced to conventions (Tier 2) or redirected at real bugs (S3). |

### Tier 3 — parked

- **Single adversarial bear-case pass** (the only debate remnant with any merit, in corrected form: one cheap LLM call arguing *against* a contemplated swing entry, judge sees the **evidence** not the rhetoric). Only after S1's deterministic core has 30+ sessions of output and only as decision-support.
- **Reddit RSS fetcher hygiene** — technically clean (keyless RSS search, Retry-After honoring, honest sample-size labels), but Indian-stock Reddit coverage is thin and any social factor must clear `research/` gates first.

---

## 6. Do-NOT-copy list (unanimous across auditors)

1. **The multi-agent debate topology.** ~10–13 LLM calls/ticker/day; termination is a fixed round budget, not convergence; **no ablation anywhere in the repo measures whether debate improves decisions over one well-prompted call.** Cost without evidence.
2. **Transcript-only judging.** Their Research Manager and Portfolio Manager never see the analyst reports — they grade debate rhetoric. If we ever add an LLM judge, raw evidence goes in its context.
3. **Assigned-conclusion personas.** "Actively champion high-reward, high-risk opportunities" tells the model its conclusion before the data; their aggressive "risk" analyst is explicitly tasked with defending the trader's decision — engineered pro-action bias inside the risk step. Personas may own a *lens* (costs, liquidity, crowding), never a *verdict*.
4. **LLM as final risk gate.** Their PM gate has no position limits, no cost model, no veto rules. Our deterministic risk stack must never be replaced or front-run by an LLM opinion.
5. **yfinance-based outcome grading.** Grade from our ledger's actual fills and charges; a third-party close series reintroduces measurement error at exactly the ~8bps scale our research identified as the leak.
6. **The 5-tier Buy/Overweight/Hold/Underweight/Sell vocabulary.** Meaningless against a long-only ₹-book making enter/skip/exit + size decisions.
7. **String-prefix routing on LLM output** (`startswith("Bull")`). They shipped a crash-class fix to paper over it. Route on typed fields.
8. **17-provider LLM abstraction.** Their changelog is dominated by provider-quirk maintenance. One provider, two models, one thin function.
9. **Alpha Vantage / StockTwits / Polymarket for NSE.** 25 req/day free tier with ~no NSE coverage; StockTwits doesn't index NSE symbols; Polymarket has ~no NSE-relevant markets.
10. **Daily-rolling OHLCV cache** (date-in-filename → full re-download per symbol per day, unbounded orphan growth). We have a paid feed and our own bar store.
11. **Error prose as successful tool results** (their fundamentals paths) — skips vendor fallback and feeds error text into the LLM data channel.
12. **LLM sentiment 0–10 score as a live input.** Unvalidated signal in structured-output clothing; anything like it goes through `research/` gates first (n≥2000, OOS, PBO, DSR), never straight into prompts.

---

## 7. Recommended sequence

Total verified near-term work: **~3–3.5 days**, all deterministic, zero LLM spend.

1. **S1 deterministic core (~1–1.5d)** — alpha-vs-NIFTY on every closed trade + entry-reasons carried onto outcome records, in SQLite, surfaced on `/portfolio` and the weekly analysis. The only item that changes what the system *does*; it monetizes logging we already paid for and directly sharpens the vwap_ema_adx coin-flip diagnosis (stock-picking vs index tide).
2. **S3 fail-loud factory selectors (~0.25d) + S4 staleness residual (~0.5d)** — unattended-machine hardening; both address failure modes we've already been burned by.
3. **S2 error taxonomy (~1d) + S5 parquet cache signatures (~0.25d)** — fold into the next `research/`/`scan` touch; S5 especially matters before the survivorship-clean data build (priority #1) starts generating new panels.
4. **~0.1 day:** write the Tier-2 LLM conventions into EDGE_ROADMAP so the future FinBERT/LLM tone-analyst build inherits their scar tissue. Build nothing LLM-shaped until that project is funded on its own merits.
5. Re-evaluate Tier 3 (LLM bear-case pass, reflection-lesson layer) after ~30 sessions of S1 alpha data.

What this repo does **not** change: our research verdict, our priorities (survivorship-clean data remains #1), or our architecture. It donates one good loop, four small hardening fixes, and a page of conventions — taken on our terms, graded by our gates.

---

## Appendix — prompt patterns worth keeping verbatim

**A. The reflection prompt contract** (`graph/reflection.py:20-29`) — engineered around its storage destination, not its reader:
> "You are a trading analyst reviewing your own past decision now that the outcome is known. Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown). Cover in order: 1. Was the directional call correct? (cite the alpha figure) 2. Which part of the investment thesis held or failed? 3. One concrete lesson to apply to the next similar analysis. Be specific and terse. Your output will be stored verbatim in a decision log and re-read by future analysts, so every word must earn its place."

Human turn is outcome-first: `Raw return: {+x.x%}\nAlpha vs {benchmark}: {+y.y%}\n\nFinal Decision:\n{text}`.

**B. Anti-confabulation grounding** (`market_analyst.py:51`):
> "…treat it as the source of truth for any exact OHLCV, price-level, or indicator-value claim. If another tool's output conflicts with the verified snapshot, flag the discrepancy rather than inventing a reconciled number. Do not claim historical validation, support/resistance bounces, or exact percentage moves unless they are directly supported by tool output with concrete dates and prices."

**C. No-fabrication data sentinels returned as the tool result** (`dataflows/interface.py:243-259`):
> "NO_DATA_AVAILABLE: No usable market data for '{sym}' from any configured vendor… Do not estimate or fabricate values — report that data is unavailable for this symbol."
> "DATA_UNAVAILABLE: optional {category} could not be retrieved ({first_error}). Proceed without it; do not fabricate values."

**D. Anti-fence-sitting judge instruction** (`research_manager.py` + mirrored in schema field description):
> "Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced."

**E. Data-sufficiency confidence rubric baked into the schema** (`schemas.py SentimentReport`): confidence low/medium/high with a *checkable* rubric in the field description ("low when one or more sources returned a placeholder or fewer than 5 data points") — the model grades its own input coverage against stated criteria instead of vibes.

---

*Sources: full clone at v0.3.1 (01477f9); GitHub issues #168, #203, #225, #814, #984, #988, #1088, #1089; commits e111388, 8a22594; arXiv:2412.20138 (paper, v7), arXiv:2510.07920 (Profit Mirage), arXiv:2505.07078 (FINSABER, KDD 2026), arXiv:2512.02261 (TradeTrap), arXiv:2509.11420 (Trading-R1); TradingAgents-CN fork.*
