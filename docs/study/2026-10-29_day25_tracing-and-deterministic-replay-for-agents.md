# Day 25 — Tracing and deterministic replay for agents; turning a production failure into a test
*Week 4: AI and agent systems engineering · ~60 minutes · 2026-10-29*

**Why this matters in a founding-engineer interview** — Agents fail in ways that don't reproduce: the model is stochastic, the tools hit live systems, and the context at failure time is gone by the time you look. The engineer who builds "every production failure becomes a regression test within an hour" owns the startup's reliability loop, and AI-infra founders (evals, tracing, replay is literally a product category) will probe whether you can design it.

## 1. Primer (10 min)

**Tracing** records what an agent run did, as a tree of timed, attributed steps. **Replay** re-executes a recorded run, either to *inspect* it (view-only), to *re-run it against new code* (counterfactual), or to *turn it into a test*.

Vocabulary:
- **Trace**: one end-to-end run (one user request, one agent session). **Span**: one step inside it — an LLM call, a tool call, a retrieval, a sub-agent. Same model as OpenTelemetry (trace_id, span_id, parent_id, start/end, attributes). OTel has GenAI semantic conventions for LLM spans; they have been evolving, so check the current spec version before depending on attribute names.
- **Recording**: capturing every *non-deterministic or external input* the run consumed: model responses, tool results, clock, random seeds, retrieved documents.
- **Deterministic replay**: feeding those recorded inputs back instead of calling the real thing, so your *own code* re-runs identically. Note what is deterministic: your orchestration logic, not the model. You replay the model's *recorded output*; you don't expect the model to say it again.
- **Cassette / VCR pattern**: record HTTP (or SDK) interactions once, replay from file. Same idea as `vcrpy`, Temporal's event history, or a market-data tape.
- **Live re-run vs frozen replay**: frozen replay answers "does my new orchestration code handle this exact sequence?"; live re-run answers "does the new prompt/model behave better on this input?" You need both and they test different things.

```
 production run                          failure → test pipeline
 ┌──────────────┐  spans   ┌──────────┐   flag    ┌──────────┐  minimize  ┌───────────────┐
 │ agent loop   │────────▶│ trace    │─────────▶│ triage   │──────────▶│ case:          │
 │  llm  tool.. │ (async) │ store    │ (user    │ (30 sec) │  + scrub   │  input + tool  │
 └──────────────┘         └──────────┘  👎, err, └──────────┘  PII       │  cassette +    │
                                        budget)                          │  assertion     │
                                                                         └──────┬────────┘
                                         CI: frozen replay (free, exact) ◀──────┤
                                         nightly: live re-run (costs $, noisy) ◀┘
```

## 2. Deep dive (25 min)

### 2.1 What a useful trace contains (and what teams forget)

A span that only says "LLM call, 2.1s, 1,840 tokens" can't reproduce anything. For replay you need the **full inputs and outputs**, not summaries:

| Capture | Why it matters for replay |
|---|---|
| Exact prompt sent (post-templating, post-truncation) | Most "the model got dumber" bugs are context assembly bugs |
| Model id + version string, temperature, tool schema, stop reasons | Providers update aliases; "same model" may not be |
| Raw model output including tool-call arguments | The thing you'll replay |
| Tool name, args, **result, error, latency** | Frozen-replay fuel |
| Retrieved chunk ids *and* their text at that time | The index changes tomorrow; ids alone don't reproduce |
| Prompt/code version (git sha, prompt hash), feature flags | Lets you say "regressed in release X" |
| Tenant id, user id (hashed), cost, token counts | Per-tenant debugging and budgets (Day 26) |
| Step index and the agent's state snapshot (or enough to rebuild) | Resume-from-step-N replay |

Forgotten most often: **the context-assembly step** (what got truncated/dropped to fit the window) and **the retrieval snapshot**.

### 2.2 Sources of non-determinism, and how to pin each

1. **Model output** — pin by recording; replay returns the recorded completion. Temperature 0 does *not* guarantee identical outputs across calls/versions (batching and provider-side changes); treat it as "less noisy", not deterministic.
2. **Tools with side effects** — replay must *never* re-execute them. In frozen replay every tool is a stub returning the recorded result. In live re-run, side-effecting tools point at a sandbox or a dry-run mode. This is the single most important safety rule: a replay harness that can send a real refund is an incident generator.
3. **Time and randomness** — inject a clock and RNG (`now()` passed in, not called). Same discipline as durable execution from Day 23: orchestrator code must be replay-safe.
4. **Concurrency order** — parallel tool calls return in nondeterministic order. Record the order the orchestrator *observed*, or make aggregation order-independent.
5. **External data drift** — retrieval index, web pages, DB rows. Snapshot what the run saw into the trace (store text, not pointers).

### 2.3 A minimal recorder/replayer

The core is a wrapper around the two boundaries (LLM, tools). Roughly 60 lines gets you an eval-grade cassette:

```python
import json, hashlib
from dataclasses import dataclass, field

@dataclass
class Cassette:
    steps: list = field(default_factory=list)   # [{"kind","key","output"}]
    cursor: int = 0

def _key(kind, payload) -> str:
    return hashlib.sha256(json.dumps([kind, payload], sort_keys=True).encode()).hexdigest()[:12]

class Boundary:
    """mode='record' calls real fn and appends; mode='replay' returns recorded output."""
    def __init__(self, cassette: Cassette, mode: str):
        self.c, self.mode = cassette, mode

    async def call(self, kind, payload, real_fn):
        k = _key(kind, payload)
        if self.mode == "record":
            try:
                out = {"ok": await real_fn(**payload)}
            except Exception as e:                      # record failures too
                out = {"err": f"{type(e).__name__}: {e}"}
            self.c.steps.append({"kind": kind, "key": k, "output": out})
        else:
            step = self.c.steps[self.c.cursor]
            self.c.cursor += 1
            if step["kind"] != kind:
                raise ReplayDivergence(f"expected {step['kind']}, agent did {kind}")
            # key mismatch = new code asked something different: divergence, not an error to hide
            if step["key"] != k:
                raise ReplayDivergence(f"{kind} args changed")
            out = step["output"]
        if "err" in out:
            raise RecordedError(out["err"])
        return out["ok"]

class ReplayDivergence(Exception): ...
class RecordedError(Exception): ...
```

Design choice worth saying out loud: **divergence is a signal, not a failure to paper over.** If you fix the bug by changing which tool is called, strict replay will diverge at that step — correct! At that point you either (a) switch to *prefix replay* (replay recorded steps up to the divergence, then go live/stubbed), or (b) assert on the *new* behavior. Strict mode is for "orchestration bug" cases (the model was fine, your loop mishandled the tool error); prefix mode is for "the model chose badly" cases.

### 2.4 Failure → test: the pipeline

1. **Detect.** Cheap triggers: exception, budget overrun, loop-guard trip (>N steps), user thumbs-down/regenerate, escalation to human, judge-on-sample score below threshold. Start with the first four — free signals.
2. **Triage (30 seconds).** Is it (a) orchestration bug, (b) tool/data bug, (c) prompt/model quality, (d) a bad user request that should be refused? The label determines the test type.
3. **Minimize.** Cut the trace to the shortest run that still fails — drop irrelevant turns, keep the failing tool result. Like delta-debugging; shorter cassettes are cheaper to read and less brittle.
4. **Scrub.** Remove PII/secrets *before* the case enters the repo. Replace with consistent fakes (same fake name each time so references still resolve). In India: customer data from an NBFC or broker is regulated; treat "traces contain customer PII" as a design requirement, not an afterthought — retention limits, access control, redaction at write time.
5. **Assert on the failure, not on the transcript.** Bad: "output equals recorded text." Good: "`issue_refund` called ≤ 1 time", "final message contains no account number", "run ends within 8 steps", "state.status == 'escalated'". Deterministic scorers from Day 24.
6. **Commit** as a case in the golden set with metadata: `source_trace_id`, `failure_class`, `risk`, date. Now it's a regression test and a record of why it exists.

Two tiers in CI: **frozen replay** (no model calls, ms each, exact, runs on every PR, zero cost) catches orchestration regressions; **live re-run** (real model, sandboxed tools, run N times, report pass-rate) catches prompt/model regressions and runs nightly or on prompt changes.

### 2.5 Build vs buy, 5 users vs 1M

- **5 users, 2 weeks:** log one JSON line per step (trace_id, step, kind, input, output, latency, cost) to Postgres or even files. A `replay.py <trace_id>` script and a `tests/cases/*.json` folder. Total: a day or two. Use a hosted tool (Langfuse, LangSmith, Braintrust, Phoenix/Arize; self-hostable options exist — verify current pricing and licensing yourself) if it's quicker than building, *but* make sure you can export raw traces; the data is the asset.
- **~1M users:** the problem becomes storage cost and privacy. Rough estimate: an agent run with 10 LLM calls, 8K tokens of context each, is ~300 KB of text uncompressed; at 1M runs/day that's ~300 GB/day raw (an estimate; compression and dedup of repeated system prompts/context cut this by 5–10×). So: sample (keep 100% of errors/flagged/high-risk, 1–5% of the rest), store prompts content-addressed (hash → blob) so shared prefixes dedupe, tier to object storage with a short hot window, async export so tracing never blocks the request path, and per-tenant retention. Tracing failures must be swallowed — **the tracer must not be able to take down the agent**.
- Head-based sampling (decide at start) is simple but misses rare failures; tail-based (decide at end) keeps all failures but requires buffering. For agents, tail-based on "error or flagged" is usually worth it.

### 2.6 Connect to your experience

- **NSE feed:** a market-data tape *is* a cassette. Recording the websocket frames around the reconnect storm and replaying them into the reconnect state machine is exactly frozen replay: the "model" (exchange) is the nondeterministic party; your reconnect gate and backoff ladder are the deterministic code under test. The incident (1,426 reconnects → IP block) becomes a test: "given this recorded sequence of disconnects and 429s, the gate must cap attempts at X per minute." That is the story of today's whole lesson in one sentence.
- **pykiteconnect #237:** a ping loop that never pings is invisible without traces of *what the code did over time*; the regression hid in a commit titled "downgrade urllib3". Lesson: assertions on behavior (a ping frame observed within 10 s) beat reading diffs.
- **DhanHQ-py #65:** the failure only appeared inside a running event loop — a *context* dependency. Same class as an agent bug that appears only with a particular tool-result ordering: record the context, then reproduce it.
- **LinkedIn:** you've likely seen request-tracing/call-graph tooling at scale; the lens to bring is *sampling and cardinality discipline* and privacy-by-default. Anchor on a real number from your own history rather than a generic one.

(Say "submitted patches" for the OSS work unless merge status is verified.)

### 2.7 Worked example

Support agent refunds a customer twice. Trace shows: step 4 `issue_refund(order=991)` → tool timed out client-side (but succeeded server-side); step 5 the model saw the timeout error and retried `issue_refund(order=991)`.
- Classify: tool + orchestration bug (non-idempotent tool, no idempotency key; the model was behaving reasonably).
- Minimize: system prompt + user message + steps 3–5, tool results stubbed (step 4 = timeout error).
- Assertion: `count(tool=issue_refund) == 1 OR idempotency_key identical across calls` and "final message acknowledges uncertainty".
- Fix: add idempotency key derived from (order_id, intent) at the tool layer — which is Day 5, applied. The frozen-replay case fails before the fix, passes after, forever.

## 3. Exercise (15 min)

Using your NSE feed project, write the **failure → test** artifact for the reconnect-storm incident. On paper or in a file, in 15 minutes:

1. (5 min) Define the cassette format: what exactly do you record? Minimum: timestamped events `{t, kind: "disconnect"|"connect_ok"|"http_429"|"frame", detail}` and the reconnect decision log `{t, action: "attempt"|"suppressed", reason}`.
2. (5 min) Write a ~25-line pytest (pseudo-code OK) that feeds a recorded event stream into your reconnect gate with an injected fake clock, and asserts: attempts in any rolling 60 s window ≤ your cap; after a 429 the next attempt respects the penalty ladder step.
3. (5 min) Write the 4-line **case metadata** (source incident, failure class, risk, what the assertion protects) and one sentence on how you'd keep customer-identifying data (client IDs, tokens) out of the committed fixture.

**Done =** one cassette schema, one test with an injected clock and a numeric assertion, one metadata block. If you can say "this test fails on the pre-fix commit" and why, you've nailed it.

## 4. Interview drill (10 min)

**Q1 (0→1). You're the first engineer at an agent startup. Customers say "it did something weird yesterday." What do you build in week one?**

<details><summary>Model answer</summary>

Capture first, analyze later. Day 1: wrap the two boundaries (LLM calls, tool calls) so every step writes a JSON row — trace_id, step, full prompt, raw output, tool args/result/error, model version, prompt/code version, latency, cost — to Postgres, async and fail-open. Day 2: a trace viewer (even a simple page rendering the step list) plus a `replay <trace_id>` CLI that runs frozen replay with tools stubbed. Day 3: a "save as test case" button/script that minimizes, scrubs, and writes a case file with a deterministic assertion. I'd adopt a hosted tool only if it exports raw traces. Then the habit: every customer-reported failure gets a case before the fix merges. I'd defer dashboards, sampling strategy and judge-on-everything until volume demands them.

</details>

**Q2. What does "deterministic replay" mean for an LLM agent when the model isn't deterministic?**

<details><summary>Model answer</summary>

It means making *everything around the model* deterministic: record the model's outputs and tool results and feed them back, so my orchestration code runs identically. I'm not claiming the model reproduces; I'm testing my loop, state handling, retries, and guards against the exact sequence that failed. Time and RNG are injected, side-effecting tools are stubbed, concurrency order is recorded. If I want to test the model's behavior on the same input, that's a different tier — live re-run, N times, reporting a pass rate.

</details>

**Q3 (scale). 1M agent runs a day; tracing storage is exploding and legal is worried about PII. What do you change?**

<details><summary>Model answer</summary>

Sample by value: keep 100% of errors, flagged and high-risk-intent runs (tail-based), 1–5% of the rest. Content-address prompts and context blobs so repeated system prompts and retrieved chunks dedupe. Redact PII at write time using a tested scrubber, not at read time; per-tenant retention windows; hot store for days, object storage for weeks, then delete unless promoted into a scrubbed golden case. Export asynchronously via a queue/buffer so tracing can't add latency or take the agent down. Track trace storage per tenant as a cost line. Rough storage estimate: a few hundred KB per run uncompressed — which is why dedup and sampling matter more than bigger disks.

</details>

**Q4 (judgment). A candidate vendor pitches a full "AI observability platform." Do we buy it now?**

<details><summary>Model answer</summary>

Maybe — but only if it passes two tests: can I export raw, complete traces (prompts, outputs, tool IO) in an open format, and can it feed my CI cases? If yes and it saves me a week, buy it; the schema is the asset, not the UI. What I would *not* do now is build a bespoke tracing platform, adopt a heavy distributed-tracing stack for a single-service agent, or score every trace with an LLM judge. Until there are many tenants and real volume, structured rows plus a replay script cover 90% of the value (estimate, not measured).

</details>

**Q5. Your fix changes which tool the agent calls first. Your replayed test now fails at step 2. Bug in the test?**

<details><summary>Model answer</summary>

Not a bug — divergence is the harness reporting that behavior changed. I decide which kind of case it is. If it guarded an orchestration bug, strict replay should mostly still pass; a divergence means my fix altered more than intended, so I investigate. If it was a model-choice failure, I switch to prefix replay (recorded steps up to the decision point, then stubbed tools with the new path) and assert on the outcome — correct final state, no duplicate side effects, step budget — not on the exact path. Tests asserting an exact transcript are brittle and get deleted.

</details>

**Common mistakes that sink candidates**
- Saying "we log everything to Datadog" with no answer to *what is logged, whether it's sufficient to reproduce, and what happens to PII*. Summaries aren't replayable.
- Proposing replay that re-calls real tools or claims temperature 0 makes the model deterministic. Both signal the candidate hasn't operated an agent with side effects.

## 5. Go further (optional)

- OpenTelemetry documentation, "Semantic conventions for generative AI" (opentelemetry.io) — check the current status; these conventions have been changing.
- Temporal documentation on event history, replay and determinism constraints (docs.temporal.io) — the cleanest production model of replay-safe orchestration.
- Kleppmann, *Designing Data-Intensive Applications*, ch. 11 (stream processing: event logs and deriving state by replay) — the general principle behind recorded-input replay.

## Tomorrow

Day 26 — Cost control and multi-tenancy for LLM features: per-tenant budgets, caching, model routing.
