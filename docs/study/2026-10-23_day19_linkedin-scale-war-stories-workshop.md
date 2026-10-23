# Day 19 — Workshop: turn three LinkedIn projects into quantified scale war stories
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-23*

**Why this matters in a founding-engineer interview** — Founders have a standing worry about ex-big-company engineers: "did they build the thing, or ride the platform?" A scale story told with real numbers, a clear personal decision, and an honest account of what you'd do differently at 5 people is the strongest rebuttal you have. This is a workshop: you leave with three drafted, rehearsable stories, not new knowledge.

## 1. Primer (10 min)

**The failure mode.** Most LinkedIn-alum stories sound like this: "I worked on a large-scale pipeline serving hundreds of millions of members and improved performance." Every word is true and the interviewer learns nothing. Scale belongs to the *platform*; what the interviewer wants to hear is *your judgment inside it*.

**The five-part war story (target: 2 minutes spoken, ~280 words):**

```
1. STAKES      one sentence: what was breaking / at risk, in numbers, and why it mattered
2. CONSTRAINT  what made it hard (deadline, legacy, org boundary, no budget to rewrite)
3. YOUR CALL   the specific decision *you* made; the alternative(s) you rejected and why
4. RESULT      before → after, in units (p99 ms, $/month, hours of toil, % error), + what it cost
5. TRANSLATE   "at a 3-person company I'd have done X instead, because Y"
```

Part 5 is the part nobody does and the one founders listen for. It proves you know which of your habits were *scale tax* and which are *principles*.

**Number hygiene.** Interviewers cannot verify your numbers but they can smell vagueness.
- Prefer ratios and units you can defend: "p99 from ~800ms to ~120ms", "on-call pages from ~15/week to ~2".
- If you don't remember exactly, say "roughly" and give the order of magnitude. Never invent precision. If you genuinely can't recall, reconstruct from what you know (QPS × payload × retention) and label it an estimate.
- Check confidentiality: public-ish figures and relative improvements are fine; internal system names, exact traffic, customer data are not. "A feed-ranking pipeline" beats the internal codename. When in doubt, scale the numbers to a ratio.

**"We" vs "I".** Say "we" for outcomes, "I" for decisions. "We cut p99 by 6x; I made the call to drop the cross-region read path and I wrote the migration." Mentoring counts: "I got two juniors to own the rollout" is a team-building data point (the thing you said you want to prove).

**Choosing the three.** Pick for *range*, not for impressiveness:

| Slot | What it proves | Typical shape |
|---|---|---|
| A. Scale / performance | You understand bottlenecks, not just tools | latency, throughput, cost, a hot partition, a cache stampede |
| B. Reliability / incident | You operate under pressure and learn | an outage, a bad deploy, a migration that went wrong |
| C. Leadership / 0→1-ish | You create leverage and make product calls | a tool you built unasked, a mentee you grew, a thing you killed |

Your NSE feed story is *not* one of the three. It is the **"translate" bridge**: the 0→1, solo, real-money proof that you can do all of this without a platform team. Use it in part 5 and in the closing question.

## 2. Deep dive (25 min)

### 2.1 Anatomy of a weak vs strong story (worked example)

**Weak (LinkedIn-flavoured, generic):**
> "I worked on our data pipeline. It processed a lot of events and we had latency issues, so I optimised it and we improved latency significantly."

Problems: no stakes, no number, no decision, no alternative, "we" throughout.

**Strong (illustrative composite — replace with your real facts):**
> "**Stakes:** A downstream consumer of our event stream was falling behind during evening peaks — lag was hitting around 20 minutes, and a product surface that promised 'near real-time' was visibly stale. **Constraint:** the stream had other consumers owned by other teams, so I couldn't change partitioning or message format. **Call:** profiling showed the consumer spent most of its time on one synchronous lookup per message. I had two options: scale out consumers — blocked, since we were capped at the partition count — or batch and cache the lookup. I chose batching with a small TTL cache, accepting up to ~60 seconds of staleness on that field, after confirming with the PM that no surface showed it. **Result:** lag went from ~20 min at peak to under a minute; consumer count dropped by about a third. The cost was a stale-field edge case I documented and put on a dashboard. **Translate:** at a 3-person company I wouldn't have needed the cache at all — I'd have put the lookup in the same Postgres query and not owned a consumer — but the habit of asking 'what staleness can this surface tolerate?' is the thing I'd carry over."

Note what it does: names the rejected option (scale-out) *and why it was blocked*; includes the cost; the PM check shows product judgment; the translation shows founder-fit.

(The numbers above are placeholders to show shape. Do not reuse them.)

### 2.2 Mining your history: a 10-minute extraction protocol

For each of your last ~5 years, answer fast, without editing:
1. What did I get paged for, or nearly paged for?
2. What did I delete or simplify? (Deleted code/services are underrated stories.)
3. What did I make faster/cheaper — and how did I *know* (which graph)?
4. What did I build that nobody assigned?
5. Who did I mentor, and what could they do afterward that they couldn't before?
6. What did I argue against in a design review and turn out right *or wrong* about?

Highlight items where you can recall a number. Stories with a recalled number beat stories without, ties go to the one with a rejected alternative.

### 2.3 Mapping stories to the Week 3 topics

The same story can answer many questions if you've tagged it. For each of your three, note which of these it supports so you can deploy it on demand:

- **Caching (Day 15):** stampede, invalidation bug, TTL trade-off.
- **Replication/consistency (Day 16):** read-your-writes complaint, stale replica.
- **Sharding (Day 17):** hot key, skewed partition, painful reshard.
- **Streams (Day 18):** lag, rebalance storm, reprocessing/replay, idempotency.
- **Incident (Day 12) / retries (Day 8):** a retry storm or cascading failure.

A founder asking "tell me about a time you debugged something nasty" and a design interviewer asking "what goes wrong with caches" are the same story with different openings. Prepare **one story, three entry points**.

### 2.4 The "translate" step in depth

Three honest translations to have ready; pick the true ones:

| LinkedIn habit | When it was right | At 3 people |
|---|---|---|
| Strict schemas / contract tests across teams | many owners, independent deploys | one repo, one types file, break it and fix it same day |
| Kafka between services | many consumers, replay, ordering at volume | Postgres table + a worker (Day 6/18) |
| Dedicated on-call + runbooks per service | rotation of 8 | a pager on one phone and a 1-page "what to check first" |
| Gradual rollout with experiment platform | 100s of millions of members, can't un-ring | feature flag + manual watch of a dashboard for 10 min |

The point isn't that the left column is wrong — it's that you can name the *load-bearing reason* for it, which means you know when it stops applying.

### 2.5 Bridging to your own 0→1 evidence

Where the translation naturally lands, use one NSE-feed sentence — never more than one or two per story. Fits:

- *Reliability/incident story →* "I later hit the same shape alone: my market-data client got stuck in a reconnect loop — 1,426 reconnects — and the exchange-side broker IP-blocked me. I added an anti-reconnect-storm gate and a 429 penalty backoff ladder. Same lesson as at work: the retry path is the least-tested code."
- *Leverage/OSS story →* "I submitted patches upstream for two broker SDKs when their websocket handling broke under asyncio and TLS/SNI, because I couldn't build on a library I couldn't trust." (Say *submitted*, not *merged*, unless verified.)
- *Scale story →* "At LinkedIn the constraint was volume; on the feed I built the constraint was correctness under a single flaky upstream with no one to page. Different bottleneck, same habit: measure first."

### 2.6 Delivery

- Time yourself. 2 minutes is the story; 30 seconds is the headline version for "tell me about yourself"; 5 minutes is the deep-dive if they dig.
- Lead with stakes, not history. "I joined the team in 2022" is a wasted 10 seconds.
- Pause after the result. Let them ask for the architecture. Handing over a story that invites follow-up is better than front-loading all detail.
- Be ready for the probe: "what would you do differently?" Have one genuine answer per story. "Nothing" signals low self-awareness.

## 3. Exercise (15 min)

Timer-driven, on paper or in a doc. Output: **a file `stories.md` with three drafts** (keep it private; it need not go in this repo).

| Min | Task |
|---|---|
| 0–3 | Run the extraction protocol (2.2). List 8–10 candidates, one line each. |
| 3–5 | Pick three, one per slot A/B/C. Tag each with the Week 3 topics it supports. |
| 5–13 | Draft all three in the five-part template, ~4 sentences max per part. Write the numbers you remember; mark guesses with "~". |
| 13–15 | For each, write the one-sentence "what I'd do differently" and the part-5 translation. |

**Done looks like:** each story has (a) at least one number with a unit, (b) one named rejected alternative, (c) one cost or downside you accepted, (d) a founder-scale translation, (e) a clean confidentiality pass. Tonight or tomorrow, read one aloud against a timer; target ≤ 2:15.

## 4. Interview drill (10 min)

1. **"Tell me about the hardest scaling problem you've worked on."** (scale)
<details><summary>Model answer</summary>

Use story A in the five-part shape, ~2 minutes: stakes with a number, the constraint (often org-boundary or deadline rather than technical), the decision *and the option you rejected*, before/after in units, the cost you accepted. Close with one sentence: "At a smaller company the same problem wouldn't exist because X, but the habit I'd keep is measuring where time goes before choosing a fix." Then stop and let them probe.
</details>

2. **"You've only worked at a big company. How do I know you can build from zero?"** (0→1 / fit)
<details><summary>Model answer</summary>

Don't get defensive; concede the gap and show evidence. "Fair concern — big companies hide the cost of building the foundation. So I did it alone: a real-time NSE market-data system, from broker websocket auth through reconnect handling to storage, running against real money. I hit a production-shaped incident — a reconnect loop that got my IP blocked — and fixed it with a gate and backoff ladder. And at LinkedIn I [story C: something unassigned I built]." Evidence of range (platform-scale judgment + solo ownership) beats assertion.
</details>

3. **"Tell me about a time something you shipped broke in production."** (incident)
<details><summary>Model answer</summary>

Story B. Order: detect (how did you find out — alert or customer?), mitigate first (rollback/flag, not root-cause), then cause, then the *system* fix (not "I was more careful"). Name the blameless-postmortem outcome: a test, alarm, or guardrail that now exists. Show ownership without self-flagellation, and give timestamps in rough minutes (time to detect, time to mitigate).
</details>

4. **"What from LinkedIn would you NOT bring to a 4-person startup?"** (judgment)
<details><summary>Model answer</summary>

Pick two concretely and give the reason each was right at scale: cross-team contract tests and schema registries (right with many independent owners; here a shared types module and fast breakage is cheaper), Kafka-by-default between services (right for fan-out and replay at volume; here a Postgres table and a worker, revisit when a second consumer or replay need appears), heavyweight experiment/rollout process (a flag and a dashboard suffices). Add what you *would* keep: measuring before optimising, blameless postmortems, small reversible deploys.
</details>

5. **"Tell me about a time you made someone else on your team better."** (leadership)
<details><summary>Model answer</summary>

Story C with a concrete before/after for the person: "When they started they could X only with help; after [the specific thing I did — pairing on design docs, handing them a service with me as backstop, rewriting the review checklist] they owned Y independently and later led Z." Include one thing that didn't work and what you changed. Founders hear: "this person multiplies the team."
</details>

**Common mistakes that sink candidates**
- **The platform-credit story.** Describing the system's scale as if it were your achievement; the interviewer immediately asks "what did *you* do?" and the answer is thin.
- **No cost, no alternative.** A story where the first idea worked perfectly with no downside reads as either fabricated or low-complexity. The rejected option and the accepted cost are what make it credible.

## 5. Go further (optional)

- *Designing Data-Intensive Applications*, Kleppmann — Chapter 1 ("Reliable, Scalable, and Maintainable Applications") gives vocabulary (load parameters, percentiles, tail latency) that makes your numbers precise.
- Google SRE Book, "Postmortem Culture: Learning from Failure" (sre.google/sre-book) — the template for story B's structure.
- Re-read your own Day 12 incident-command STAR story and check it against today's five-part template.

## Tomorrow

Day 20 — Feeds: fan-out on write vs fan-out on read.
