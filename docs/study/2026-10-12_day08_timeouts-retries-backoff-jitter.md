# Day 08 — Timeouts, retries, backoff and jitter; retry storms
*Week 2: Reliability (your essay's theme) · ~60 minutes · 2026-10-12*

**Why this matters in a founding-engineer interview** — Every 0→1 design eventually calls something flaky (a broker feed, a payment API, an LLM provider), and the interviewer is checking whether your "retry on failure" is a one-liner that melts the dependency or a bounded, budgeted, jittered policy. Retry storms are the most common self-inflicted outage at small companies, and you have a real one with a number attached.

## 1. Primer (10 min)

**Mental model.** A retry is a bet that the failure was transient. Every retry also *adds load* to a dependency that may be failing because of load. So a retry policy is a load-generating policy; it needs a budget, a spacing rule and a stop rule.

**Vocabulary.**
- **Timeout**: connect timeout (TCP/TLS handshake), read/idle timeout (gap between bytes), total/deadline timeout (whole operation). Most libraries default to one of these being *infinite* or very large — check.
- **Deadline propagation**: caller's remaining time budget passed downstream, so the callee doesn't work on a request nobody is waiting for.
- **Backoff**: increasing wait between attempts (exponential: `base * 2^n`, capped).
- **Jitter**: randomisation of the wait so clients desynchronise. Full jitter = `uniform(0, min(cap, base*2^n))`.
- **Retry budget**: a cap on retries as a fraction of traffic (e.g. retries ≤ 10% of requests over a window), or a per-process token bucket.
- **Retry amplification**: with depth D and N attempts per layer, one user request can become N^D calls.
- **Idempotent / safe to retry**: only if repeating produces the same effect (Day 5 idempotency keys, Day 6 idempotent consumers).
- **Retry-After**: server-provided wait (429/503); obey it over your own computed backoff.
- **Metastable failure**: a state where the retry load itself sustains the outage after the original trigger is gone.

```
Without jitter (all clients fail at t=0, backoff 1,2,4s):
load  ▲   █          █              █
      │   █          █              █      <- synchronised waves hit the
      │   █          █              █         recovering server together
      └───┴──────────┴──────────────┴──▶ t
With full jitter: the same retries spread roughly uniformly across each window.

Amplification:  user ─▶ API ─▶ svc A ─▶ svc B ─▶ DB      (3 attempts at each hop)
                1 request  → up to 3 × 3 × 3 = 27 DB calls when the DB is the thing that's slow
```

## 2. Deep dive (25 min)

### 2.1 Timeouts come first
A retry policy without timeouts is meaningless: a hung call never "fails", so it never retries, and it holds a socket, a worker and memory. Rules of thumb:
- Set **every** outbound call's timeout explicitly. Pick from the dependency's observed p99 (×2–3), not a round number. Estimate: a healthy same-region service is p99 of tens of ms; a third-party API commonly 300 ms–2 s.
- Timeouts must **nest**: the caller's timeout > callee's timeout × (attempts) is wrong. Make the outer deadline the source of truth and give inner calls what's left.
- A timeout does **not** cancel the work on the other side. After a timeout you do not know whether the write happened. This is why retrying a non-idempotent POST is the bug, and why Day 5's idempotency key exists.

### 2.2 What to retry
| Condition | Retry? |
|---|---|
| Connect error, DNS blip, connection reset | Yes (if idempotent, or failure was before send) |
| Timeout | Yes, **only** if idempotent; state is unknown |
| 429 / 503 with Retry-After | Yes, after the stated wait |
| 500/502/504 | Yes, bounded |
| 400/401/403/404/422 | No — deterministic; retrying is noise (401 → refresh creds once, then stop) |
| Bug in your own code (exception parsing the response) | No |

### 2.3 Backoff + jitter, concretely
Three common variants (AWS Architecture Blog, "Exponential Backoff And Jitter", compares these in simulation):
- **Full jitter**: `sleep = uniform(0, min(cap, base * 2**attempt))`. Best at spreading load and cutting total client work.
- **Equal jitter**: half fixed, half random. Guarantees some minimum wait.
- **Decorrelated jitter**: `sleep = min(cap, uniform(base, prev_sleep * 3))`.
Default to full jitter unless you have a reason. Cap it (30–60 s typical for interactive paths, minutes for background jobs). **Always** have a max attempts *and* a max elapsed time.

```python
import random, time

def call_with_retry(fn, *, attempts=4, base=0.2, cap=5.0, deadline_s=10.0, retryable=lambda e: True):
    start = time.monotonic()
    for n in range(attempts):
        try:
            return fn(timeout=max(0.05, deadline_s - (time.monotonic() - start)))
        except Exception as e:
            last = e
            if not retryable(e) or n == attempts - 1:
                raise
            sleep = random.uniform(0, min(cap, base * 2**n))
            if time.monotonic() - start + sleep >= deadline_s:
                raise            # no point sleeping past the deadline
            time.sleep(sleep)
    raise last
```
Note what it does: the remaining deadline shrinks each attempt; retryability is a predicate, not "any exception"; it won't sleep past the budget.

### 2.4 Retry storms and why they're structural
A dependency slows (say 5% errors). Each client now sends up to N× traffic. The dependency, already stressed, errors more. Retry load pushes it into overload and it stays down even after the trigger is fixed (metastable). Defences, in order of cost:
1. **Retry only at one layer** (usually the edge or the outermost caller). Inner layers fail fast. This alone kills the exponential amplification.
2. **Jitter + cap** so clients don't synchronise.
3. **Retry budget / token bucket**: e.g. each success deposits 0.1 token, each retry costs 1; when empty, don't retry. (Pattern used in gRPC retry throttling and Envoy/Finagle-style budgets; exact parameters are implementation-specific.)
4. **Honour Retry-After** and treat 429 as a *signal to slow down globally*, not just for that request.
5. **Circuit breaker / load shedding** (Day 9).
6. **Server side**: shed load early, return cheap 503s; make the failing path cheaper than the success path.

### 2.5 Reconnect is just retry on a long-lived connection
Reconnect loops are the nastiest case: no request boundary, the loop runs forever, and nobody sees it as "a retry policy". A tight reconnect loop + a server that rate-limits connection attempts = ban.

### 2.6 5 users vs 1M users
- **5 users / 2 weeks**: a tiny helper (like above) around every outbound HTTP call, explicit timeouts, 3 attempts, full jitter, and **one log line per retry** with the attempt number. Don't build a framework. Use a library (`tenacity` in Python) if you must, but still set timeouts yourself.
- **1M users**: retry budgets as a shared per-service component, deadline propagation across RPC (gRPC deadlines), per-dependency dashboards for retry ratio (retries/requests — alert at e.g. >10%, estimate), hedged requests for tail latency on idempotent reads (cost: extra load; use sparingly), and load tests that include a "dependency at 50% errors" scenario.

### 2.7 Worked example: your 1,426-reconnect incident
Frame it as a retry-policy failure with numbers:
- **Trigger**: feed disconnect (transient).
- **Naive policy**: reconnect immediately, no ceiling → 1,426 reconnects before the broker blocked the client IP.
- **Why it escalated**: the dependency (broker endpoint) *rate-limits connection attempts*; each failed attempt consumed budget you didn't own. A policy that is safe against *failure* can still be unsafe against *the dependency's own defences*.
- **Fixes you built**: (1) an **anti-reconnect-storm gate**: a minimum interval/attempt cap that holds even if upstream callers keep asking, i.e. the budget is enforced at the connection chokepoint, not in each caller; (2) a **429 penalty backoff ladder**: a rate-limit response moves you up a ladder of longer waits and resets only after sustained health; (3) **auth separation**: a TOTP/login failure must not be retried like a network error (deterministic failure class, §2.2).
- **Lesson to say aloud**: "the retry policy is part of the dependency contract. I now treat reconnect as a budgeted resource with a single gate, because the count of attempts is a number I'd previously never bounded."

LinkedIn tie-in (use only what you can honestly claim): if you've seen a downstream slowdown propagate upward through service layers, tell it as amplification — "each hop retried, so the bottom service saw a multiple of real traffic" — and name the layer where you'd centralise retries.

## 3. Exercise (15 min)
**Task: write `RetryPolicy` + a simulation that proves jitter and budgets matter.** Python, stdlib only, 15 minutes, timer on.

1. (6 min) Implement `backoff(attempt, base, cap, mode)` for `"none" | "exp" | "full"`.
2. (6 min) Simulate: 1,000 clients, a server that handles ≤100 requests/second and returns errors for anything over. All clients' first call fails at t=0. Each client retries with the chosen mode (max 5 attempts). Bucket requests per 100 ms and record peak load and how many clients eventually succeed.
3. (3 min) Write 4 lines under the code: which mode had the lowest peak, which had the highest success rate, and what a retry budget would have changed.

**Done =** the script prints a small table (mode, peak req/100ms, success %), full jitter has a visibly lower peak than `exp`, and your 4-line conclusion states the metastable risk of `none`. Stretch (if time): add a token-bucket retry budget and show the peak drops again.

## 4. Interview drill (10 min)

**Q1 (0→1). "Your service calls a payments API that sometimes times out. Design the client."**
<details><summary>Model answer</summary>
Explicit connect/read/total timeouts from the provider's observed p99. A timeout means unknown outcome, so every payment POST carries an idempotency key generated *before* the first attempt and stored with the order; retries reuse it. Retry only connection errors, timeouts, 429/503 (honouring Retry-After), 3 attempts, full jitter, a total deadline under the user-facing timeout. Log each attempt with the key. If still failing, don't retry in-request forever — mark the payment "pending", enqueue a reconciliation job (Day 6) that polls the provider for status by key. Not building yet: circuit breaker, multi-provider failover.
</details>

**Q2 (scale). "Latency from a downstream service rises 5×; 20 minutes later your whole fleet is down. What happened and what do you change?"**
<details><summary>Model answer</summary>
Likely a retry/timeout cascade: slow calls hold threads/connections, timeouts fire, callers retry at each layer, load multiplies on the already-slow service (amplification N^depth), and queues/connection pools exhaust upstream. Even after the trigger recovers, retry load can keep it down (metastable). Changes: retry at one layer only; deadlines propagated and shrinking; jittered capped backoff; per-service retry budget (retries ≤ ~10% of traffic); bulkheads and a circuit breaker to fail fast; server-side load shedding; alert on retry ratio, not only error rate; game-day test with dependency at 50% errors. During the incident: shed load or disable retries via a flag first, then investigate.
</details>

**Q3 (judgment). "What would you NOT build for retries in the first month?"**
<details><summary>Model answer</summary>
No retry framework, service mesh, shared budget service or hedging. One small helper with explicit timeouts, 3 attempts, full jitter, per-attempt log lines, and idempotency keys on writes. I would add a retry-ratio metric early because it's cheap and tells me when to invest. I'd revisit when there are more than ~3 services calling each other or an incident shows amplification.
</details>

**Q4. "Why jitter? Doesn't exponential backoff already spread load?"**
<details><summary>Model answer</summary>
Exponential backoff spaces each client's retries out, but clients that failed at the same moment retry at the same moments (1s, 2s, 4s), creating synchronised waves. Jitter randomises the wait so the aggregate arrival rate becomes smooth. Full jitter generally gives the lowest peak load and total completion work in simulation (AWS Architecture Blog comparison).
</details>

**Q5. "Is it safe to retry a request that timed out?"**
<details><summary>Model answer</summary>
Only if the operation is idempotent or protected by an idempotency key, because a timeout means the outcome is unknown: the server may have completed it. For reads, yes. For writes, send a key and let the server dedupe. Without that, prefer to surface "unknown" and reconcile rather than blindly retry.
</details>

**Common mistakes that sink candidates**
- Saying "exponential backoff" and stopping — no jitter, no cap, no max attempts, no timeout, no idempotency.
- Retrying at every layer, or retrying non-retryable errors (4xx, auth), or treating a timeout as "the request failed".

## 5. Go further (optional)
- AWS Architecture Blog, "Exponential Backoff And Jitter" (Marc Brooker) — the simulation comparing full/equal/decorrelated jitter.
- Google SRE Book, ch. 22 "Addressing Cascading Failures" (sre.google/sre-book/addressing-cascading-failures/) — retries, load shedding, deadlines.
- Amazon Builders' Library, "Timeouts, retries, and backoff with jitter" (Marc Brooker).
(Links not fetched in this unattended run; titles are canonical.)

## Tomorrow
Day 09 — Circuit breakers, bulkheads, load shedding, backpressure.
