# Day 24 — Evals: offline vs online, LLM-as-judge pitfalls, regression suites
*Week 4: AI and agent systems engineering · ~60 minutes · 2026-10-28*

**Why this matters in a founding-engineer interview** — In an AI-native startup the eval suite is the test suite, the CI gate and the product spec in one, and the first engineer is usually the person who decides whether it exists. Interviewers are checking that you can say "better" in a way a machine can verify, without building a research platform in week two.

## 1. Primer (10 min)

An **eval** is a function: `(input, system_output, [reference]) → score(s)`, run over a **dataset** of cases, aggregated into numbers you compare across versions of the system (prompt, model, retrieval config, tool set). That's it. Everything else is where the cases come from and who or what does the scoring.

```
 production traffic ──sample/flag──▶ triage ──▶ labeled case ──▶ golden set (versioned)
        ▲                                                           │
        │                                                           ▼
   online evals                                         offline run: candidate vs baseline
 (shadow, A/B, user                                      │  scorers: code | judge | human
  signals, judge-on-sample)                              ▼
        ▲                                         report: pass-rate, per-slice delta, cost, p95
        └──────────── ship / don't ship ◀──────────────┘
```

Vocabulary:
- **Offline eval**: fixed dataset, run before shipping. Fast, repeatable, cheap, but only as representative as the dataset.
- **Online eval**: measured on live traffic — explicit signals (thumbs, edits, refunds issued, escalations), implicit signals (retry/regenerate, abandonment), or a judge scoring a sample of real traces. Representative, but slow, noisy, and sometimes irreversible (a bad answer already reached a customer).
- **Scorer types**, in order of preference: (1) **deterministic code** (exact match, regex, JSON schema valid, "called `issue_refund` ≤ 1 time", SQL result equal); (2) **model-as-judge** against a rubric; (3) **human review**. Use the cheapest scorer that can actually detect the failure.
- **Golden set / regression suite**: curated cases that must not get worse. **Capability set**: harder cases you expect to fail today, tracking progress.
- **Trajectory vs outcome eval** (agents): did the final state come out right, vs did it take acceptable steps (right tools, no duplicate side effects, within budget). Outcome first; trajectory checks for safety and cost.
- **Pass@k vs pass^k**: pass@k = succeeds at least once in k tries; pass^k = succeeds on *all* k tries. Users experience the second. A 90% per-run agent is ~59% on 5 consecutive runs (0.9^5) — a number worth quoting.

## 2. Deep dive (25 min)

### 2.1 The dataset is the product

Where cases come from, best first:
1. **Real production failures** — every bug report, escalation or bad trace becomes a case (Day 25 goes deep on this). Highest signal, free.
2. **Real production sample** — random sample of traffic, labeled. Protects against only testing the failures you've already noticed.
3. **Hand-written from domain knowledge** — founders and support staff know the nasty cases. 20–50 of these in week one is worth more than a 5,000-row synthetic set.
4. **Synthetic** (LLM-generated variations) — good for expanding coverage around a seed case; bad as the *only* source, because the generator and the system share blind spots and the cases look unnaturally clean.

Size guidance (estimates, not laws): 30–50 cases is enough to catch large regressions and start the habit; a few hundred supports per-slice reporting; you need *much* more to detect small improvements. Rough statistics: with n = 100 and a 80% pass rate the standard error is ~4 points, so a 3-point "improvement" is noise. Run each case multiple times if the system is stochastic, and report confidence intervals or at least paired per-case diffs (which cases flipped), not just the aggregate.

**Slice, don't just average.** An aggregate going 82% → 84% can hide a 20-point drop on refunds over ₹10k. Tag cases (`intent`, `tenant tier`, `language`, `risk=high`) and report by tag. Weight by *cost of failure*, not frequency.

**Version the dataset** like code (JSONL in the repo is fine at seed stage). Never silently edit cases to make a run pass.

### 2.2 Offline vs online: what each is for

| | Offline | Online |
|---|---|---|
| Question | "Is candidate ≥ baseline on known cases?" | "Is the real world better off?" |
| Latency | minutes | days–weeks |
| Risk | none | real users |
| Blind spot | distribution drift, unknown unknowns | confounds, low sample on rare-but-costly cases |
| Gate | CI, pre-merge | rollout %, kill switch |

The healthy loop: offline gates the merge → shadow or canary (1–5% of traffic, or shadow mode where the new version runs but its output isn't shown) → online metrics confirm → failures flow back into the offline set. Skipping the back-edge is how eval suites go stale.

**Shadow mode** is the founding-engineer-friendly trick: run the candidate on live inputs, store its outputs, score them offline against the incumbent. You get real distribution with zero user risk — only a side-effect-free path (read-only tools or stubbed writes) makes this safe for agents.

### 2.3 LLM-as-judge: useful, and full of traps

A judge is a model prompted with a rubric to score an output. It's the only way to scale evaluation of open-ended text, and it fails in known ways:

- **Position bias**: in pairwise comparisons, judges favour the first (or sometimes second) answer. Mitigate: run both orders, count only consistent wins.
- **Verbosity / style bias**: longer, more confident, more formatted answers score higher regardless of correctness.
- **Self-preference**: a model tends to favour outputs resembling its own. Use a different model family from the system under test where practical.
- **Rubric vagueness**: "rate helpfulness 1–10" yields mushy, unstable scores. Prefer **binary or 3-level criteria with definitions** ("Does the reply state the refund amount that appears in the order record? yes/no") and decompose one fuzzy score into several crisp ones.
- **Reference leakage and shortcutting**: give the judge the ground-truth facts (order record, policy text) so it checks *consistency with evidence* rather than judging plausibility from its own priors.
- **Non-determinism and drift**: judge model updates change your scores. Pin the judge model/version, temperature 0, and re-baseline when you bump it. (Provider-side determinism isn't guaranteed even at temp 0 — treat as best-effort.)
- **Cost**: judging with a strong model can cost more than the system call. Judge a sample online; judge everything offline only where cheap.

**Validate the judge like any classifier.** Hand-label 50–100 cases, compute agreement (raw agreement and Cohen's κ) between judge and human, and look at the confusion matrix — especially false passes on bad outputs. If agreement is poor, fix the rubric before trusting any number it produces. Re-check periodically. "We use GPT as a judge" without this step is a classic hand-wave interviewers probe.

### 2.4 Regression suites for agents

Layer them by cost:

1. **Unit-level, deterministic (every commit, seconds)**: tool-argument validation, parsers, guardrails, idempotency-key derivation. No model call.
2. **Replay/mocked-model tests (every commit)**: recorded model responses + stubbed tools, assert state-machine behaviour — "crash after tool call, resume, tool not re-executed" (Day 23's refund bug). Deterministic because the model is replaced by a recording.
3. **Live-model golden set (on PR touching prompts/tools/model; nightly)**: 50–300 cases, real model calls, mix of code scorers and a validated judge. Gate on: no regression on `risk=high` slice, aggregate within tolerance, cost and p95 latency not > X% worse.
4. **Capability/stress set (weekly)**: harder or adversarial cases (prompt-injection attempts, ambiguous requests); tracked, not gated.

Gate design: use **thresholds with tolerance plus a "no previously-passing high-risk case flips to fail" rule**, rather than "score must not drop" — with stochastic systems, strict monotonic gates produce flaky CI and the team learns to ignore red. Retry a failed case a few times before declaring it a flip, and surface flips in the PR comment so a human can overrule with a reason.

### 2.5 5 users vs 1M users

- **5 users, 2 weeks**: a JSONL file of 30 cases, a 100-line Python runner (Day 27 drill), code scorers where possible, one judge with a 3-line rubric, results printed as a diff against last run, run on demand and in CI for prompt changes. Look at traces by hand daily — error analysis beats dashboards at this size. Don't build a UI.
- **1M users**: sampled online judging with stratified sampling by tenant/intent, drift alerts on score distributions, human-review queue with active selection (low judge confidence, negative user signals, high-risk intents), per-tenant eval slices, experimentation platform with proper A/B stats, eval cost as a budgeted line item. At this point it's a data pipeline: traces → sample → score → warehouse → dashboard + alerts.

### 2.6 Worked example: eval for a refund-agent change

Change: switch the support agent from the current model to a cheaper one. Cases (JSONL):

```json
{"id":"r-017","tags":["refund","risk=high"],
 "input":"Order 8841 arrived broken, I want my money back",
 "state":{"order":{"id":8841,"total_inr":4200,"delivered":true,"refunded":false}},
 "expect":{"tool_calls":{"issue_refund":{"max":1,"args":{"order_id":8841,"amount_inr":4200}}},
           "must_not":["issue_refund_on_undelivered"]}}
```

```python
def score_case(case, trace) -> dict:
    calls = [s for s in trace.steps if s.kind == "tool"]
    refunds = [c for c in calls if c.name == "issue_refund"]
    exp = case["expect"]["tool_calls"]["issue_refund"]
    return {
        "refund_count_ok": len(refunds) <= exp["max"],            # code: duplicate-side-effect check
        "refund_args_ok": all(r.args == exp["args"] for r in refunds),  # code: right amount, right order
        "within_budget": trace.steps_used <= 8 and trace.cost_usd < 0.05,
        # judge only what code can't see: tone and whether reply matches the refund actually issued
        "reply_consistent": judge(rubric=CONSISTENT_REPLY, reply=trace.final, evidence=case["state"]),
    }
```

Note how most of the verdict is deterministic: the dangerous failure (double refund, wrong amount) is a code check. The judge covers only the part that needs language understanding. Report: per-tag pass-rate, list of flipped cases, cost per case, p95 latency. Decision rule: ship if `risk=high` has zero new fails across 3 runs each, aggregate ≥ baseline − 2 pts, cost down ≥ 30%.

### 2.7 Connect it to your experience

Your market-data work is an eval culture in disguise: the reconnect storm that got your IP blocked (1,426 reconnects) was a failure path nobody had a test for — "the code that handles failure is the code nobody tests." An eval suite is that essay as infrastructure: every incident becomes a case that fails today and must pass forever. Likewise, your pykiteconnect #232 work (TLS without SNI, proven via a loopback ClientHello capture) is the right instinct — build a deterministic harness that observes the actual behaviour instead of trusting that it works. At LinkedIn scale you've lived the online side: A/B tests, guardrail metrics, ramp percentages. Say that explicitly: "online evals are A/B testing with a judge as one of the metrics."

## 3. Exercise (15 min)

**Task: write the eval plan for an AI support agent that can look up orders and issue refunds up to ₹5,000 (one-page markdown, hand or keyboard).**

Include, in order, 3 minutes each:
1. **Failure taxonomy**: list 6 distinct ways it can fail, each tagged *outcome* or *trajectory* and with the cheapest scorer that can catch it (code / judge / human). At least two must be deterministic-code checkable.
2. **Dataset v0**: sources for the first 40 cases (how many from each of the 4 sources in §2.1), tags you'd use, and which slice is `risk=high`.
3. **Judge rubric**: write one binary rubric (≤ 3 lines, with evidence given to the judge) for "reply is consistent with what the agent actually did."
4. **Gate**: the ship/no-ship rule, with numbers, and what happens when a gated case is flaky.
5. **Online**: two signals you'd monitor in production and what each one sends back to the offline set.

**Done looks like**: one page; every failure mode maps to a scorer; the gate has explicit numbers; you can say in one breath why you'd not build an eval dashboard yet.

## 4. Interview drill (10 min)

**Q1 (0→1). You join as the first engineer; the LLM feature has no tests and the founder edits prompts live. What do you do in week one?**

<details><summary>Model answer</summary>

Don't start with tooling. Day 1–2: read 50–100 real traces with the founder and write down failure modes in plain language — that's error analysis, and it tells me what to measure. Then convert 30–50 cases into a JSONL golden set (real failures first, hand-written risky cases second), tag them, and write a ~100-line runner with code scorers for the objective failures and one judge only for what code can't see. Wire it to CI on any prompt/tool/model change and make prompts live in the repo, not in a database or dashboard, so changes are reviewable and runnable against the suite. Founder keeps editing prompts — but now with a number next to the change. I'd explicitly not build a UI, a platform or a 1,000-case synthetic set yet.

</details>

**Q2. How do you trust an LLM judge?**

<details><summary>Model answer</summary>

Treat it as a classifier I have to validate. Hand-label 50–100 cases, measure agreement and inspect false passes — wrongly passing a bad answer is the dangerous direction. Use narrow binary rubrics with the evidence supplied, a pinned judge version at temperature 0, a different model family from the system under test where possible, and for pairwise comparisons run both orders and require consistency to counter position bias. Where a code check can replace the judge, I replace it. And I re-validate when I change the judge model, since scores aren't comparable across judges.

</details>

**Q3 (scale). Traffic is 5M conversations/day. How does your eval strategy change?**

<details><summary>Model answer</summary>

Evaluating everything is unaffordable and unnecessary. Cheap deterministic checks and guardrail metrics run on 100% of traffic (schema validity, tool-call counts, budget overruns, refund-per-ticket anomalies). Judge scoring runs on a stratified sample — oversampling high-risk intents, new tenants, and cases with negative signals like escalations or regenerations. A human-review queue is fed by active selection (low judge confidence, judge/user-signal disagreement), and reviewed cases feed the golden set. Releases go through shadow → canary with guardrail metrics and auto-rollback. Score distributions per slice are monitored for drift, and eval spend is a tracked budget, often ~1–5% of inference spend (an estimate; set by what the risk justifies).

</details>

**Q4 (judgment). The team wants to block merges unless the aggregate eval score never decreases. Good idea?**

<details><summary>Model answer</summary>

No — with stochastic outputs and a few hundred cases, a strict non-decrease rule trips on noise, CI becomes flaky, and engineers learn to rerun until green or bypass it. I'd gate on a tolerance for the aggregate plus a hard rule on the small `risk=high` slice (no previously-passing case flips after retries), report flips in the PR, and allow an override with a written reason. Strictness should follow cost of failure, not tidiness of the number.

</details>

**Q5. Our eval score went up 5 points but customers complain more. What happened?**

<details><summary>Model answer</summary>

Offline set no longer represents production, or the metric isn't what customers care about. Check: dataset staleness versus recent traffic mix; slices — gains in easy, frequent intents masking losses in costly ones; judge bias (e.g. rewarding longer, politer replies that don't resolve the issue); optimisation against the eval (prompt tuned to the 50 known cases — hold out a set nobody tunes on); and outcome measures like resolution and re-contact rate that the offline score doesn't capture. Fix: sample recent complaints into the set, add a held-out split, and add an outcome-based online metric.

</details>

**Common mistakes that sink candidates**
- Proposing "use an LLM to grade it" as the whole answer, with no mention of validating the judge, deterministic scorers, or what the judge's failure modes are.
- Building the platform before the habit: dashboards and synthetic datasets of thousands, while the founder still has no 30-case regression file. Founders read that as inability to ship small.

## 5. Go further (optional)

- Hamel Husain, "Your AI Product Needs Evals" and related writing on error analysis (hamel.dev) — practical, startup-sized eval practice. (Cite by title; I haven't verified a deep link.)
- Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (2023, arXiv) — the primary paper documenting position, verbosity and self-preference biases.
- Anthropic, "Building effective agents" — evaluation advice for agent systems; and Yao et al., "τ-bench" (2024, arXiv) for the pass^k reliability metric.

## Tomorrow

Day 25 — Tracing and deterministic replay for agents; turning a production failure into a test.
