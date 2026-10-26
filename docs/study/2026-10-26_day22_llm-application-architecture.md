# Day 22 — LLM application architecture: context, retrieval basics, latency and cost budgets
*Week 4: AI and agent systems engineering · ~60 minutes · 2026-10-26*

**Why this matters in a founding-engineer interview** — Most AI-native startups are hiring employee #1–3 to make an LLM feature *reliable, fast and affordable*, not to invent a model. Interviewers listen for whether you treat the model as an unreliable, slow, metered dependency, which is the same posture you already have toward a flaky market-data feed. The signal is naming budgets (tokens, latency, dollars) before drawing boxes.

## 1. Primer (10 min)

An LLM application is a **request pipeline around a stateless function**: `f(prompt tokens) → completion tokens`. The model remembers nothing between calls; everything it "knows" at inference time is in the context window you assemble.

```
user request
   │
   ▼
[ input guard ]──reject/redirect
   │
   ▼
[ context assembly ] ◄── system prompt (static, cacheable)
   │                ◄── retrieved docs (RAG)
   │                ◄── conversation history (trimmed/summarised)
   │                ◄── tool results / user data
   ▼
[ model call ] ── streaming tokens ──► [ output guard / parser ] ──► user
   │                                          │
   └── retry/fallback model, timeout ◄────────┘ (schema fail → repair or retry)
   ▼
[ log: prompt version, model, tokens in/out, latency, cost, trace id ]
```

Vocabulary:
- **Context window** — max tokens (input + output) per call. Larger windows are not free: cost and latency scale with input, and quality on long contexts degrades unevenly ("lost in the middle" is a documented effect, though severity varies by model).
- **TTFT** (time to first token) vs **TPOT / inter-token latency** — TTFT is dominated by *prefill* (reading your input); the rest is *decode* (generating output, roughly serial, one token at a time).
- **RAG** — retrieval-augmented generation: fetch relevant text at request time and put it in the prompt, instead of fine-tuning knowledge in.
- **Embedding / vector search** — map text to vectors; nearest-neighbour search finds semantically similar chunks. **Hybrid search** = vector + keyword (BM25), usually merged then **reranked**.
- **Chunking** — splitting docs into retrievable units (commonly a few hundred tokens, with overlap or structure-aware splits).
- **Prompt caching** — providers cache a stable *prefix* of the prompt so repeat calls pay less and prefill faster. Mechanics, discounts and TTLs are provider- and version-specific; check current docs.
- **Structured output** — constraining/validating the response against a schema (JSON schema, tool-call arguments).

## 2. Deep dive (25 min)

### 2.1 Budgets first

Three budgets, set before design:

| Budget | Typical founding-stage target | What drives it |
|---|---|---|
| Latency | Chat: TTFT < ~1–2 s, full answer streamed. Background/batch: minutes OK | input size (prefill), output length (decode), number of *sequential* model calls |
| Cost | Cost per *successful task* (not per call), against price per seat or per resolved ticket | tokens in × price_in + tokens out × price_out, times calls per task |
| Quality | An eval score you can regress against (Day 24) | model choice, context quality, prompt |

Rules of thumb (all **estimates**, vary hugely by model and year; recompute with current price sheets):
- Output tokens cost several times input tokens per token (often ~3–5×), and decode is the slow part. **Shorter outputs are the cheapest latency and cost lever.**
- Decode speed is on the order of tens of tokens/s per request for large models, faster for small ones. A 500-token answer is therefore several seconds; that's why you stream.
- Sequential calls add. A "planner → retriever → answerer → critic" chain with 1.5 s each is 6 s before network. Every extra hop needs to earn its keep in eval numbers.
- Tokens ≈ 0.75 English words (rough); code and non-English text tokenize worse, which matters for Indian-language products.

### 2.2 Worked example: cost per task

A support-answer feature. Per request: 800-token system prompt + 3 retrieved chunks × 400 = 1,200 + 300 history + 100 question ≈ 2,400 input tokens; 250 output tokens.

Illustrative (made-up) prices: $3 / M input, $15 / M output.
- Input: 2,400 × $3/M = $0.0072
- Output: 250 × $15/M = $0.00375
- ≈ **$0.011 per call**. 

Now the real question: calls per *task*. If 30% of tasks need a retry on schema failure and 20% use a follow-up clarification, effective calls/task ≈ 1.5+, and an "agentic" version with 6 tool loops multiplies input again because each loop re-sends the growing context. At 100k tasks/month: $1.1k at 1 call, ~$6–7k at the agentic version. Margin math decides whether the agentic design is allowed.

Levers, in the order I'd pull them:
1. **Cut context** — better retrieval (fewer, more relevant chunks) beats a bigger window.
2. **Prompt caching** — put static content (system prompt, tool defs, few-shot examples) *first* and stable; put per-request content last. Anything that changes early in the prompt (a timestamp, a user name at the top) invalidates the cached prefix. This is the same discipline as cache-key design on Day 15.
3. **Model routing** — a small model for classification/extraction/routing, a large model only for hard generation. Gate it on eval data, not vibes (Day 26 goes deeper).
4. **Shorter output / structured output** — return IDs and short fields, render in your code.
5. **Semantic/response caching** — only for FAQ-shaped traffic; risky for anything personalised or where staleness matters.
6. **Batch APIs** for non-interactive work — providers commonly offer a discount for async batch (verify current terms).

### 2.3 Retrieval basics — and when *not* to use a vector DB

Failure modes in RAG are mostly *retrieval* failures, not generation failures: the right chunk never reached the prompt. Diagnose by logging retrieved chunk IDs per request and checking "was the answer in the context?" separately from "did the model use it?"

Practical ladder:
- **v0 (5 users, 2 weeks):** if the corpus fits in context (say < 50–100k tokens) and changes rarely, **stuff it all in the prompt** and use prompt caching. No retrieval infra. This is a real, defensible choice, and a lot of "RAG" startups skip this step.
- **v1:** Postgres full-text search or `pgvector` on the Postgres you already have (Day 3). One datastore, transactional with your metadata, easy tenant filtering with a `WHERE tenant_id = …`. Hybrid: BM25-ish `tsvector` rank + vector similarity, merged with reciprocal rank fusion.
- **v2 (millions of chunks, high QPS, heavy filtering):** a dedicated vector index (HNSW-based, in a purpose-built store or pgvector with tuning). ANN indexes trade recall for speed; measure **recall@k against exact search** on your data. Re-embedding on model change is a full backfill — store the embedding model version with each vector.
- **Reranking:** retrieve ~20–50 candidates cheaply, rerank with a cross-encoder or small LLM to keep 3–5. Often the highest quality-per-dollar step, but it adds latency (tens to a few hundred ms estimate).

Chunking pitfalls: splitting mid-table or mid-code-block; losing the document title/section context (prepend it to each chunk); stale chunks after the source changes (key chunks by `(doc_id, version)` and delete on update); **multi-tenant leakage** — the tenant filter must be applied in the retrieval query itself, never post-hoc in app code after top-k is chosen (post-filtering also gives you fewer than k results).

### 2.4 Context assembly is where the engineering is

- Treat the prompt as a **versioned artifact** (`prompt_id@version`), logged with every call, with a changelog. A prompt edit is a deploy.
- **Trim history deliberately**: keep the last N turns verbatim, summarise older ones, always keep pinned facts. Unbounded history is a slow cost leak and a latency creep.
- **Untrusted content in context is an injection vector.** Retrieved documents, emails, web pages and tool outputs can contain instructions. Mitigations: separate system instructions from data with clear delimiting (helpful, not sufficient), least-privilege tools, no auto-execution of side effects from model output without validation, and output checks. State plainly that there is no complete prompt-level fix.
- **Structured output**: validate with a schema (Pydantic). On failure, one bounded repair/retry, then a fallback — the Day 8 rules apply: timeouts, capped retries, jitter, and a retry *budget* so a provider brownout doesn't triple your bill and your load.

### 2.5 Reliability: the model is a flaky upstream

Everything from Week 2 transfers directly:
- Per-call **timeouts** (TTFT timeout and total timeout are different), **circuit breaker** + **fallback model** (a different provider or a smaller model) for provider outages and 429s.
- **Rate limits are in tokens/min as well as requests/min.** A 429 backoff ladder like the one you built for Dhan applies; add client-side token accounting so you queue instead of hammering.
- **Streaming** changes failure handling: you may have sent half an answer when the stream dies. Decide: retry silently and replace, or surface "something went wrong, regenerate". Don't stream content to the user that you'd still want to validate/guard.
- **Idempotency**: if an LLM call triggers a side effect (send email, place order), the idempotency key belongs to *your* action layer, not the model call. Retries of the model call can produce a *different* action.
- Non-determinism: temperature 0 reduces but doesn't guarantee reproducibility (provider/hardware/version drift). Log everything needed to replay (Day 25).

### 2.6 Scale story: 5 users vs 1M users

| | 5 users, ship in 2 weeks | 1M users |
|---|---|---|
| Retrieval | Stuff context, or pgvector in existing Postgres | Dedicated index, hybrid + rerank, tenant-sharded, freshness pipeline |
| Models | One strong model, hard-coded | Router + fallback chain, per-task model selection from evals |
| Cost control | Spend alert on the provider dashboard | Per-tenant budgets, caching layers, batch for async, unit economics dashboards |
| Quality | 20 hand-written test cases in a file | Offline regression suite + online sampling + human review queue |
| Latency | Stream the answer | Prefetch/speculative retrieval, parallelise independent calls, smaller models on the hot path |
| Ops | Log prompts to Postgres | Tracing with spans per stage, PII redaction, retention policy, data-processing agreements |

### 2.7 Connecting to your experience

Your NSE feed is the same shape: an unreliable, rate-limited, metered upstream with a ban risk. The anti-reconnect-storm gate and 429 ladder map to LLM provider brownouts: without a retry budget, a 20-minute provider degradation causes your own retry traffic to extend it and burns tokens. The Dhan/pykiteconnect patches are the same "tooling around someone else's SDK assumes the happy path" lesson — the OSS LLM SDKs have the same sharp edges (event-loop assumptions, TLS/proxy behaviour, streaming reconnects), and you've already fixed this class of bug. Use it as a one-line proof of fluency, not the headline.

A minimal budget-aware call wrapper (sketch, provider-agnostic):

```python
import asyncio, random, time
from dataclasses import dataclass

@dataclass
class Budget:
    max_attempts: int = 2
    ttft_timeout_s: float = 3.0
    total_timeout_s: float = 20.0
    max_cost_usd: float = 0.05

async def call_llm(client, req, budget: Budget, fallback=None):
    start, spent = time.monotonic(), 0.0
    for attempt in range(budget.max_attempts):
        remaining = budget.total_timeout_s - (time.monotonic() - start)
        if remaining <= 0 or spent >= budget.max_cost_usd:
            break
        try:
            resp = await asyncio.wait_for(client.complete(req), timeout=remaining)
            spent += resp.cost_usd
            return validate(resp)          # schema check; raise on failure
        except (asyncio.TimeoutError, RateLimited, SchemaError):
            await asyncio.sleep(random.uniform(0, 0.5 * 2 ** attempt))  # jitter
    if fallback:
        return await fallback(req)          # smaller model / canned response
    raise LLMUnavailable()
```

## 3. Exercise (15 min)

**Task: write a one-page "LLM Feature Budget" for a feature of your choice**, e.g. "answer questions over a company's SEBI/RBI circulars" (AI-native compliance), or "explain why this trade/strategy lost money" (markets).

Timebox: 3 min pick + users; 8 min numbers; 4 min decisions.

Done looks like, on one page:
1. **Request anatomy** with token estimates per component (system, retrieved, history, question, output) and a total.
2. **Cost per task** using *current* price sheet numbers (look them up; write the date), including expected calls per task and one retry/loop multiplier.
3. **Latency budget**: a table of stages (retrieve, rerank, TTFT, decode) summing to a target p95, with the stage you'd cut first.
4. **Retrieval choice for v0 and the trigger to move to v1** (a number: corpus tokens, QPS, or recall@k failing).
5. **Three "will not build yet" items** (e.g. no custom embeddings, no multi-model router, no semantic cache) each with the measured condition that unlocks it.
6. **Failure plan**: what happens on provider 429/timeout/schema failure, in one line each.

## 4. Interview drill (10 min)

**Q1 (0→1).** "We're a 3-person team and want a chatbot over our customers' documents. What do you build in the first two weeks?"

<details><summary>Model answer</summary>

Clarify: how many docs/tokens per customer, how fresh must answers be, is accuracy or speed more important, any compliance constraints (data residency, PII).

Then: week 1 is the thinnest end-to-end slice — upload → chunk → store in Postgres with pgvector (or just stuff the context if a customer's corpus is under ~50–100k tokens) → retrieve → one strong model → streamed answer with citations to chunk IDs. Tenant filtering inside the retrieval query. Log every call: prompt version, retrieved chunk IDs, tokens, latency, cost, trace ID.

Week 2 is the eval seed: 30–50 real questions with expected source chunks and acceptable answers, run on every prompt/retrieval change, plus a thumbs-up/down button feeding a review queue. I would not build a custom embedding model, an agent loop, a router, or a dedicated vector DB. I would add each when a measured number (recall@k, p95, cost per task) says so.
</details>

**Q2 (scale).** "Cost per task tripled after launch and p95 latency is 14 s. Walk me through it."

<details><summary>Model answer</summary>

Don't guess; use the logs. Break down by stage and by tenant/task type: tokens in/out, calls per task, retry rate, cache-hit rate. Typical culprits: (a) agent loops resending an ever-growing context; (b) a prompt change that moved a dynamic field to the top and killed prefix caching; (c) retrieval returning too many chunks; (d) retries on schema failures doubling calls; (e) one tenant with huge documents or a runaway integration.

Fixes by leverage: cap loop iterations and summarise/trim tool outputs; restore stable-prefix ordering; retrieve more, rerank, send fewer; fix the schema failure at the prompt/validation level rather than blind retrying; per-tenant budgets and rate limits. For latency: parallelise independent calls, shorten outputs, stream, move classification steps to a small model. Then add an alert on cost per task and a canary on prompt changes so it can't silently regress again.
</details>

**Q3 (judgment).** "Should we fine-tune a model on our data, or use RAG?"

<details><summary>Model answer</summary>

Default RAG (plus good prompting) for *knowledge*: it's updatable, citeable, tenant-isolable and deletable (important for compliance). Fine-tuning is for *behaviour/format/style* or distilling a large model's task performance into a cheaper one once I have an eval set and enough high-quality examples. I would not fine-tune before I have evals, since I couldn't tell whether it helped; and not to inject facts that change, since I'd have to retrain and couldn't delete one customer's data. Revisit when cost or latency at volume justifies distillation, with measured evidence from the eval suite.
</details>

**Q4 (reliability).** "The model provider has a 30-minute outage. What happens to your product?"

<details><summary>Model answer</summary>

Timeouts and a circuit breaker so requests fail fast rather than pile up; a fallback chain (second provider or smaller model, pre-evaluated so I know the quality hit); a retry budget with jitter so we don't amplify; queue and defer for async work; a clear degraded-mode message in the UI for interactive paths; and no side-effecting action taken on a partial result. Afterwards: postmortem, with the fallback path exercised regularly (a game day) because untested failover code fails when needed. I've lived the retry-storm version of this with a market-data feed where reconnect spam got my IP blocked; the gate and backoff there is the same pattern.
</details>

**Q5 (security).** "Users upload PDFs that go into your prompt. What's the risk?"

<details><summary>Model answer</summary>

Prompt injection: document text can instruct the model to exfiltrate data, ignore rules or call tools. There's no complete prompt-only defence, so I limit blast radius: tools are least-privilege and scoped to the calling tenant; side-effecting actions need validation or user confirmation; the model's output is treated as untrusted input; no cross-tenant data in the same context; output filtering for secrets/PII; and red-team cases in the eval suite. Also tenant-scoped retrieval to prevent leakage.
</details>

**Common mistakes that sink candidates**
1. Starting with "a vector database and LangChain" before stating the user, corpus size or any budget. It reads as tool-first, not problem-first.
2. Quoting cost per *call* and ignoring calls per task, retries and context growth in loops — or giving precise latency/price numbers as facts rather than labelled estimates.

## 5. Go further (optional)

- Anthropic documentation: prompt caching and the "Building effective agents" engineering article (search the title on anthropic.com/engineering).
- Lilian Weng's blog, "Prompt Engineering" and "LLM Powered Autonomous Agents" (lilianweng.github.io).
- Chip Huyen, *AI Engineering* (O'Reilly, 2025), chapters on RAG and inference optimization. Pair with DDIA ch. 3 for the indexing background behind retrieval.

## Tomorrow

Day 23 — Agents in production: tool calls, loops, state, durable execution.
