# Founding Engineer Prep — 12-Week Plan

**Started:** 2026-07-19 · **Target:** first offers by mid-October 2026, signed by November.

**Goal:** Get hired as a founding engineer (employee #1–3) by a strong, funded founder — a repeat operator, a VC-backed CEO, or a researcher-founder who needs the person who builds everything else.

**Assumptions** (flag if wrong): India-based (Bangalore), staying at LinkedIn while prepping, ~11 focused hrs/week, open to India + global-remote startups, pre-seed → Series A.

---

## 1. How this market actually works (know the game before playing it)

- **These roles are filled through networks, not job boards.** The sequence is: founder raises → asks investors and friends "who's the best engineer you know who can ship alone?" → the fund's talent partner searches their database → warm intros happen. Cold applications are the weakest channel. Your whole distribution strategy (§6) is about entering those loops.
- **What founders screen for, in order:**
  1. Evidence you ship end-to-end **without scaffolding** (no platform teams, no PM, no spec)
  2. Speed and pragmatism — bias to cut scope and ship
  3. Technical depth in *their shape* of problem
  4. Product judgment — you'll make product calls daily
  5. Commitment — will you stay through the 18-month trough
  6. Can you attract and lead engineers #2–#5
- **Credentials buy the first call; proof-of-work buys the offer.** LinkedIn on the resume gets you the intro meeting. What converts is public evidence of agency.
- **The big-tech discount is real.** Founders' #1 fear about big-co engineers: "moves in quarters, not days; can't work without the platform." Every asset you build (§3) exists to preempt this objection before it's spoken.
- **Typical loop shape:** founder intro (45m, vision + chemistry) → technical deep-dive on something *you* built (60–90m) → practical build round or take-home (2–6h, sometimes a paid trial day/weekend) → architecture conversation → founder working session → two-way references → offer. VC-routed funnels sometimes add a standard DSA screen — keep a maintenance level, don't grind.
- **A hot 2026 pattern worth targeting:** researcher-founders (especially AI) who need one person to own systems, infra, product engineering, and hiring. Your systems + ML-pipeline background fits this shape exactly.

## 2. Positioning — your narrative and existing assets

**One-breath narrative (rehearse until boring):**
> "Five years at LinkedIn across multiple teams — I've operated systems at real scale and mentored engineers to promotion. And on nights and weekends I build and operate a real-time trading system solo: market-data ingestion to risk engine to dashboards to production ops. I want to compound that as employee #1."

**Your unfair advantage: the NSE signal engine.** Most big-tech engineers claim 0→1 ability; you can *show* it. Solo-built and live-operated: WebSocket market-data ingestion, signal engine, risk manager, paper-trading ledger with real cost modeling, FastAPI backend, Next.js dashboards, Telegram alerting, schedulers, launchd watchdogs, tunnels. Plus honest research discipline: documented no-edge verdicts, cost-in-R gating, pre-registered evals. That's a founding-engineer job description in miniature: **build, operate, measure, kill bad ideas.**

**Do not hide the no-edge result — it's the asset.** "I built rigorous infrastructure, measured honestly, and published that the strategy doesn't clear costs" is a rarer and more trusted signal than "my bot makes money." Founders are drowning in hype; truth-seekers stand out.

**Target thesis — pick 2–3 domains where your story compounds:**
1. **Fintech / markets infrastructure** (obvious: your project, Indian broker APIs, real-time data)
2. **Applied AI / agent infrastructure** (you run heavily automated, agent-operated ops already)
3. **Real-time data / developer tooling**

Speaking credibly about the founder's domain in the first call is a top-decile differentiator. Generic "I'm a strong engineer" loses to "I've felt this exact pain."

**Story inventory to write down (week 1–2):**
- 3 LinkedIn **scale war stories**, quantified (QPS, data volume, migration scope, incident MTTR)
- 3 **mentoring/leading stories** with outcomes (mentee promoted, ramp time halved, cross-team conflict resolved)
- 2 **speed stories** (fastest you ever shipped something real; what you cut)
- 2 **failure stories** with honest lessons

## 3. Proof-of-work sprint (weeks 3–6) — the flagship move

Turn the trading engine into public, legible evidence:

1. **Public face:** either open the repo, or keep code private and publish a deep-dive (architecture writeup + redacted excerpts). Private-code-public-writeup is fine; founders read the writeup, not the code.
2. **Architecture doc + diagram** (C4-ish, one page): ingestion → engine → risk → paper trader → API → dashboards, plus the ops layer (launchd, watchdogs, tunnel, Telegram). This doc IS your system-design portfolio piece.
3. **Demo video, 3–5 min:** dashboards live during market hours, an alert firing, the portfolio page. Screen recording + voiceover, no production polish needed.
4. **Two essays** (cross-post: blog/Substack + LinkedIn + X thread):
   - *Systems essay:* "Operating a real-time NSE market-data pipeline solo — WebSockets, watchdogs, and the IPv6 hotspot outage that taught me ops." → systems credibility.
   - *Honesty essay:* "I built a signal engine, measured everything, and found no edge — the full accounting." → rigor + truth-seeking. This genre gets shared by exactly the founder/VC audience you want.
5. **GitHub hygiene:** pin 2 polished repos with READMEs (architecture, screenshots, how-to-run). Two excellent artifacts beat a green contribution graph.

## 4. The 12-week plan

| Weeks | Theme | Concrete outputs |
|---|---|---|
| **1–2** (Jul 20–Aug 2) | Positioning + assets | Narrative written; 10-story STAR bank v1; LinkedIn headline/profile updated; target thesis chosen; tracking sheet (CRM) created |
| **3–6** (Aug 3–30) | Proof-of-work sprint + reps begin | Architecture doc, demo video, 2 essays published; daily 1h technical reps running (§5); 1 design rep/week |
| **5–8** (Aug 17–Sep 13) | Open the funnel (overlaps) | 30 warm-intro list worked at 5/wk; talent-network profiles live (YC WaaS, Wellfound, India funds); 10 founder DMs/wk on fresh funding announcements; first calls — deliberately sequence B-tier companies first as practice |
| **7–10** (Aug 31–Sep 27) | Interview intensity | 2 mocks/wk (1 technical, 1 founder-fit); take-home timeboxing drilled; equity/ESOP study done (one evening, §8); iterate on real interview feedback |
| **9–12** (Sep 14–Oct 11) | Close | 2–3 processes in parallel; reverse diligence run on each (§7); references lined up both ways; negotiate; decide with scorecard |

**Checkpoint rule (week 8):** if you don't have ≥2 active processes, it's a *positioning* problem, not a volume problem — get 3 sharp people to critique your narrative and assets, then double outreach.

## 5. Interview curriculum, by round type

### 5a. Practical coding (the most common founding-eng round)
Not LeetCode — "build a working thing in 90 minutes": a rate limiter, a webhook receiver with retries, a CSV importer with validation, a small REST+DB feature, a realtime widget.
- **Drill:** 2×/week, 90-min timeboxed, empty repo → working demo, in your stacks (Python/FastAPI and TS/Next — you have both).
- **Muscles:** narrate scope-cuts out loud; commit hygiene; tests for the core logic only; README in the last 5 minutes.
- **DSA maintenance only:** ~3 NeetCode mediums/week (arrays, hashmaps, two-pointer, BFS/DFS, heaps). Founding loops rarely exceed medium; VC-routed screens sometimes hit medium.

### 5b. System design — practice TWO registers
- **Register A — scale (credibility):** rehearse your LinkedIn war stories as design narratives with real numbers. Proves you've seen what breaks.
- **Register B — 0→1 (judgment):** "Design v1 of X to ship in 2 weeks for 50 users; now evolve it to 50k." The skill is **sequencing**: monolith + Postgres, add a queue when needed; name the first bottleneck, the first hire, and the first thing you'd *refuse* to build. Practice saying "I would not build that yet, because…" — founders fear over-engineers more than under-engineers.
- 1 rep of each register per week; alternate mock partner and self-recorded.

### 5c. Take-homes / trial projects
- Timebox to stated hours ×1.25 max. Working > complete.
- Ship a **DECISIONS.md** — what I cut, what I'd do with another day, and why. This single file wins take-homes; it demonstrates the exact judgment they're buying.
- Paid trial days/weekends: say yes; treat them as two-way diligence.

### 5d. Product sense + founder chemistry
- Per target company, do the **2-hour prep**: use the product; write 3 things you'd improve, 1 growth idea, 1 guess at their architecture. Bring it to the call. Almost nobody does this.
- Write and rehearse crisp answers: *why leave LinkedIn now · why startups · why this domain · what I need from a founder · what would make me quit · comp expectations (§8).*
- Have 2–3 spiky, defensible opinions (e.g., "big-tech process startups shouldn't copy").

### 5e. Leadership / "can you build the team" (your §2 mentoring stories + one artifact)
- **Hiring Plan one-pager:** how I'd hire engineers #2–#5 — sourcing, loop design, my bar, first-90-days onboarding. Bring it to late rounds. It converts "claims to lead" into "already operates like a lead."
- Stories ready: mentee → promotion; incident command; cross-team conflict; disagree-and-commit.
- Founding engineer ≠ manager on day 1: the honest frame is "lead by example, then hire and lead the first team" — say exactly that.

## 6. Distribution — getting in the room

Ranked by conversion:
1. **Warm intros:** list 30 ex-colleagues / LinkedIn alumni now at startups (the LinkedIn-mafia founder network is real). 5 messages/week: specific ask — "who do you know raising seed who needs their first engineer?"
2. **VC talent networks:** India — Peak XV, Accel India, Lightspeed India, Elevation, Blume, Z47, Antler India. Global — YC Work at a Startup profile, Wellfound, a16z/Sequoia talent teams. Talent partners are findable on LinkedIn; a crisp note + your flagship link gets you into the database they search when founders ask. *(Verify current program details — per your own rule on current sources.)*
3. **Direct founder outreach:** track fresh seed/Series-A announcements (Entrackr, Inc42, YourStory for India; TechCrunch and X for global). DM within days: 2 specific sentences about *their* problem + your proof link. 10/week. Specific beats long.
4. **Content compounding:** your two essays make inbound possible; engage genuinely with founders/VCs on X — comments with substance, not "great post."
5. **Cofounder-track adjacents (optional):** Entrepreneur First (Bangalore) and Antler literally pair "big shot with idea" with technical talent; South Park Commons if US-leaning. These are cofounder-flavored — decide if you want that before applying.

**Track everything** in a sheet-CRM: company, stage, founder, channel, status, next action, date. Friday retro: funnel counts (sent → replies → calls → processes).

## 7. Reverse diligence — a founding engineer joining a bad founder loses 2–4 years

Ask founders directly (asking well *raises* their opinion of you):
- Runway in months, at what burn? Revenue/usage truth today?
- Cap table: founder ownership %, any dead equity?
- Why hasn't this worked before? What's the sharpest version of the bear case?
- How do decisions get made when we disagree?
- "Give me 2 references — an ex-employee and an investor." Then actually call them.

**Scorecard (use it, don't vibe):** founder quality 40% · market/idea 25% · terms 20% · learning & energy 15%. Never join for terms alone.

## 8. Equity & comp — study one evening, then set your floor

Sanity ranges (verify current benchmarks via Carta/AngelList/Pave data + peer intel; these drift):
- **India founding engineer (pre-seed→A):** cash ≈ ₹35–80L, equity ≈ 0.5–2% (higher at pre-seed / first hire).
- **US / global-remote:** cash ≈ $140–200k, equity ≈ 0.5–3% at pre-seed/seed.
- Expect cash 20–40% below your LinkedIn total comp. **Decide your floor before the first call** so it never leaks mid-interview.

Terms checklist — negotiate equity harder than cash:
- % **fully diluted** (share count alone is meaningless — get the denominator)
- Vesting 4yr/1yr-cliff standard; ask about **post-departure exercise window** (push 90 days → longer)
- Strike price / FMV; **double-trigger acceleration** on acquisition
- Expected ESOP-pool refresh dilution over next 2 rounds
- **India-specific:** ESOP tax hits at *exercise* (perquisite tax on FMV−strike) plus capital gains at sale; the DPIIT-startup deferral is narrow. Model post-tax outcomes before valuing the equity. *(Rules shift with budgets — verify FY26-27.)*
- Read: Holloway Guide to Equity Compensation (one evening covers 90% of this).

## 9. Weekly cadence (~11h while employed)

- 5 × 1h weekday reps — rotate: practical build / DSA maintenance / design register A or B
- 3–4h weekend block — proof-of-work sprint (then mocks after week 6)
- 1.5h outreach + CRM upkeep
- 30m Friday retro against milestones (§4)

## 10. Resources (curated, not exhaustive)

- **Design/systems:** DDIA (Kleppmann, chs 1–9 selectively); Alex Xu System Design vols 1–2 (as prompt banks); LMAX architecture essay; Zerodha tech blog (minimal-stack pragmatism, India-fintech credibility — directly your story); engineering blogs of Discord/Figma/Cloudflare for war-story vocabulary.
- **Mocks:** interviewing.io; Pramp; or a standing weekly trade with 2 peers.
- **DSA maintenance:** NeetCode 150 (mediums only).
- **Founder-world literacy:** YC Library + Work at a Startup; First Round Review; Lenny's Newsletter episodes on founding engineers and product sense.
- **Equity:** Holloway Guide to Equity Compensation; current India ESOP-tax primers.
- **Outreach triggers:** Entrackr / Inc42 / YourStory funding feeds; TechCrunch; X.

*(Per your standing rule: verify programs/links/benchmarks against current sources before relying on them.)*

## 11. Anti-patterns — what NOT to do

- ❌ Grind 300 LeetCode problems — wrong game; practical builds are the game.
- ❌ Say "we" for everything — founders hire an *I*; own your part precisely.
- ❌ Speak in big-co process language (alignment, roadmap, stakeholders) — speak in shipped / measured / learned.
- ❌ Hide the no-edge verdict — the honesty is the differentiator.
- ❌ Six months of silent prep — start real conversations by week 5; the market teaches faster than the curriculum.
- ❌ Negotiate title — founding engineer + equity + scope IS the package.
- ❌ Join the first founder who flatters you — run the §7 scorecard every time.
