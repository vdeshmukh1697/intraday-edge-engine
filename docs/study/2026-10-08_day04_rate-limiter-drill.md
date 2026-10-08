# Day 04 — Practical drill: a rate limiter in 90 minutes (token bucket vs sliding window) + DECISIONS.md
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-08*

**Why this matters in a founding-engineer interview** — The 90-minute practical round is where founders see you ship alone and narrate cuts. A rate limiter is a favourite prompt because it is small enough to finish, yet hides real choices: algorithm, clock source, concurrency, where state lives, and what happens on failure. The artifact they remember is not the code but the `DECISIONS.md` that says what you deliberately did not build.

---

## 1. Primer (10 min)

### The full drill (90 min) — today you do milestone 1 only

> "Build a rate limiter as a small FastAPI service/library. Clients are identified by API key. Limit: 100 requests/minute with bursts allowed. Return 429 with `Retry-After`. It should be correct under concurrency. Write a `DECISIONS.md`."

| Milestone | Time | Done means |
|---|---|---|
| **M1 (today)** | 0–60 min | In-memory token bucket, injectable clock, unit tests (burst, refill, per-key isolation), FastAPI dependency returning 429 + `Retry-After` |
| M2 (later) | 60–80 | Sliding-window-counter variant behind the same interface, + a test comparing boundary behaviour |
| M3 (later) | 80–90 | `DECISIONS.md`, and a sketch (not code) of the Redis version |

### Vocabulary

| Term | Meaning |
|---|---|
| Fixed window | Counter per `floor(now/window)`. Simple; allows 2× burst at a window boundary. |
| Sliding window log | Store every timestamp; exact; O(limit) memory per key. |
| Sliding window counter | Weighted blend of current and previous fixed window; approximate, O(1) memory. |
| Token bucket | Bucket of capacity `B` refilled at rate `r` tokens/s; a request spends tokens. Allows bursts up to `B`, long-run rate `r`. |
| Leaky bucket | Like token bucket but smooths output to a constant rate (queue). Shaping vs policing. |
| Policing vs shaping | Reject excess (429) vs delay it. APIs almost always police. |

### Mental model

```
 tokens
  B ┤▇▇▇▇▇▇▇▇            burst drains the bucket
    │       ▇▇▇▇
    │           ▇▇       refill at r tokens/sec (lazy: computed on next request)
  0 ┼────────────────── time
```

Key trick: **lazy refill**. Don't run a timer per key. Store `(tokens, last_ts)`; on each request compute `tokens = min(B, tokens + (now - last_ts) * r)`. State is two floats per key.

---

## 2. Deep dive (25 min)

### Token bucket vs sliding window — pick on behaviour, not fashion

| | Token bucket | Sliding window counter | Sliding window log |
|---|---|---|---|
| Memory/key | 2 numbers | 2 counters + window id | up to `limit` timestamps |
| Burst semantics | Explicit (`B`) | Implicit, approx. | Exact N in any window |
| Accuracy | Exact for its own definition | Approximate (assumes uniform distribution in prev window) | Exact |
| Retry-After | Easy: `(cost - tokens)/r` | Awkward | Computable from oldest timestamp |
| Fits | Public APIs, "100/min with burst 20" | Cheap approximate quotas at huge key counts | Low-limit, high-value (login attempts: 5/15 min) |

Default for a startup: **token bucket**. It is the easiest to explain, easy to compute `Retry-After`, and burst is a product knob you can tune per plan.

### The decisions that actually separate candidates

1. **Clock.** Use `time.monotonic()`, not `time.time()`. Wall clocks jump (NTP step, VM migration) and a backwards jump gives negative refill or a stuck bucket. Inject the clock (`clock: Callable[[], float]`) so tests are deterministic and need no `sleep`. In a distributed version you cannot use per-process monotonic clocks across nodes, so you take time from Redis (`TIME`) inside the script — a real design point.
2. **Concurrency.** In a single asyncio process, a read-modify-write with no `await` in the middle is atomic by construction — the event loop is the lock. The moment you add `await` between read and write (e.g. calling Redis in two round trips), you have a race. With threads (sync FastAPI endpoints run in a threadpool) you need a `threading.Lock`. State this explicitly; it's the answer to "is this thread-safe?"
3. **Key choice.** API key, user, IP, or tenant? IP limits punish NAT'd offices and are trivially bypassed by botnets; API key is what you want post-auth, IP as a coarse pre-auth backstop (login endpoints).
4. **Cost per request.** Make `try_acquire(key, cost=1)` take a cost. Expensive endpoints (export, LLM call) cost more. Costs one parameter now; saves a rewrite later.
5. **Memory growth.** A dict keyed by client grows forever — an attacker rotating keys/IPs is a memory DoS. Fix: evict keys idle long enough that their bucket would be full anyway (full bucket == no state). Cheap sweep every N calls, or an LRU cap.
6. **Fail-open vs fail-closed** (distributed version). If Redis is down, do you block all traffic or allow all? For a general API: fail open (availability beats precision), with an alert and a local per-process fallback limit. For auth/SMS-OTP endpoints where abuse costs real money: fail closed. Name the asymmetry.
7. **Response contract.** `429 Too Many Requests`, `Retry-After: <seconds>` (integer seconds is the universally supported form). The `RateLimit-*` headers exist as an IETF draft (`draft-ietf-httpapi-ratelimit-headers`) — say it is a draft; many APIs still use `X-RateLimit-*`. Round `Retry-After` **up**, or clients retry just too early and get 429 again.

### Scale ladder

- **5 users / 2 weeks:** in-process token bucket, one container. Legit; document that limits are per-process.
- **Multiple app instances:** per-process buckets multiply the real limit by N. Options: sticky routing (fragile), or shared state in Redis.
- **Redis:** one Lua script (atomic) doing refill + spend, `EXPIRE` on the key to ~time-to-full. One round trip, ~sub-ms to ~1ms in-region (estimate). A single Redis comfortably handles tens of thousands of such ops/s (estimate; benchmark yours). Beyond that: shard by key, or go **approximate** — each node takes a local slice of the quota and syncs periodically, trading exactness for no hot path network hop.
- **1M users / edge:** enforce at the gateway/CDN layer (cheap rejection before hitting app), plus app-level quotas for business semantics. Rate limiting (protect the system, per-second) differs from quotas (billing, per-month); don't conflate them in one component.

### Worked example: the lazy-refill core

```python
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable
import math

@dataclass
class _Bucket:
    tokens: float
    last: float

@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float  # seconds; 0 if allowed
    remaining: float

class TokenBucketLimiter:
    def __init__(self, rate: float, burst: float,
                 clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()  # needed if called from threads; harmless under asyncio

    def try_acquire(self, key: str, cost: float = 1.0) -> Decision:
        if cost > self.burst:
            raise ValueError("cost exceeds burst; request can never succeed")
        now = self.clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = self._buckets[key] = _Bucket(self.burst, now)
            elapsed = max(0.0, now - b.last)          # guard clock weirdness
            b.tokens = min(self.burst, b.tokens + elapsed * self.rate)
            b.last = now
            if b.tokens >= cost:
                b.tokens -= cost
                return Decision(True, 0.0, b.tokens)
            wait = (cost - b.tokens) / self.rate
            return Decision(False, wait, b.tokens)
```

Note the denied path does **not** spend tokens — a client hammering at 429 must not push itself further into debt, but also must not be rewarded. (Contrast: you may deliberately *penalise* abusers, e.g. the 429 penalty backoff ladder you built against Dhan — that is a different policy layered on top.)

### Connect to your experience

- **NSE feed:** you were on the *receiving* end of a rate limiter. 1,426 reconnects got your IP blocked; your anti-reconnect-storm gate is a client-side token bucket on connection attempts, and your 429 penalty ladder is a client honouring `Retry-After`. Interview line: "I've built the client half of rate limiting after being on the wrong side of the server half — so I design limiters with `Retry-After` and jitter guidance, because a limiter without a polite-client story just creates synchronized retry waves."
- **LinkedIn:** you've seen quotas enforced at a platform layer versus per-service. Use that for the "rate limit vs quota" distinction (tell it as an observation, don't claim specific numbers you can't back).

---

## 3. Exercise (15 min of the 60; the rest is building)

Set a timer. Suggested split of today's hour: **10 min** write the interface and 5 test names on paper, **35 min** implement + tests, **15 min** FastAPI wiring (this block).

**Task (milestone 1):** in a scratch directory create `limiter.py`, `test_limiter.py`, `app.py`.

1. Implement `TokenBucketLimiter` above from memory (peek only when stuck).
2. Tests with a fake clock (a small class with `.now` and `.advance(s)`), **no `time.sleep`**:
   - burst: `burst=5` allows exactly 5 immediate calls, 6th denied
   - refill: after denial, `advance(1/rate)` allows exactly one more
   - isolation: key A exhausted does not affect key B
   - cap: after very long idle, tokens never exceed `burst`
   - retry_after: value is correct to 1e-9 and, when followed, the next call succeeds
3. FastAPI dependency: reads `X-API-Key` (400/401 if missing), calls the limiter, raises `HTTPException(429, headers={"Retry-After": str(math.ceil(d.retry_after))})`.
4. Add the idle-key eviction sweep **or** write it in the "not built" list — either is acceptable, silent omission is not.

**Done looks like:** `pytest` green; `curl` loop shows 5 × 200 then 429 with a sane `Retry-After`; a 6-line `NOT_BUILT.md` stub listing: distributed state, fail-open policy, per-plan limits, metrics, eviction (if skipped). That stub seeds the real `DECISIONS.md` in M3.

---

## 4. Interview drill (10 min)

**Q1 (0→1). "We have a public API with no limits and one customer just took us down. You have two weeks. What do you do?"**
<details><summary>Model answer</summary>

First stop the bleeding without code: identify the customer from logs and apply a block or reduced limit at the load balancer/WAF if one exists. Then ship an in-process token bucket keyed by API key as FastAPI middleware: one afternoon, tests with a fake clock, 429 + `Retry-After`, a log line and counter on every rejection. Defaults generous (set from observed p99 per-key traffic, not guesses) so I don't break legitimate customers. I would explicitly not build Redis-backed distributed limiting yet: with 2–3 instances the effective limit is 2–3× nominal, which is still enough to protect us. Next sprint: shared state if instance count grows or precision matters, per-plan limits, and a customer-facing doc of the limits.
</details>

**Q2 (scale). "Now you have 50 app servers and 20M keys. Design it."**
<details><summary>Model answer</summary>

Per-process buckets multiply limits by 50, so I centralise. Redis with a Lua script doing atomic refill-and-spend, one round trip, key TTL ≈ time-to-full so idle keys vanish; time taken from Redis `TIME` to avoid cross-node clock skew. Shard by key hash across a Redis cluster. For the hot path cost, I'd put a coarse limiter at the edge/gateway and keep the precise one for expensive endpoints only. For extreme keys (one tenant doing 100k rps) use local token leases: each node borrows a batch of tokens and refills asynchronously, accepting bounded over-admission. Failure mode: Redis down → fail open to a conservative per-node local limit and page, except on money-costing endpoints (OTP SMS) which fail closed.
</details>

**Q3. "Token bucket or sliding window — why?"**
<details><summary>Model answer</summary>

Depends on the promise I'm making. Token bucket when I want an explicit burst allowance and cheap state (two numbers) and easy `Retry-After`; that's most public APIs. Sliding window log when "no more than N in any window" must be exact and N is small — login attempts. Sliding window counter when I need O(1) memory at huge key counts and can tolerate a few percent of error. Fixed window only for coarse quotas, since it permits 2× bursts at boundaries.
</details>

**Q4 (judgment). "What would you NOT build in the first version?"**
<details><summary>Model answer</summary>

Distributed state, dynamic per-plan config UI, rate-limit headers beyond `Retry-After`, adaptive/ML-based throttling, and a separate limiter service. Each solves a problem I don't yet have evidence of. What I *would* build is the instrumentation — rejected-count by key — because that data tells me which of those I need next.
</details>

**Q5. "Is your limiter thread-safe? How would you test it?"**
<details><summary>Model answer</summary>

Under a single asyncio loop the read-modify-write has no `await`, so it's atomic; under FastAPI sync endpoints (threadpool) I hold a lock. Test: N threads each call `try_acquire` with a frozen fake clock and `burst=100`; assert exactly 100 allowed total regardless of N and interleaving. A frozen clock removes timing flakiness, so the test is deterministic.
</details>

**Common mistakes that sink candidates**
- Starting with Redis/Lua before anything works. Founders read this as inability to ship v1; the 90 minutes is scored on a finished thing plus honest cuts.
- Tests with `time.sleep` (slow, flaky) or using `time.time()` — signals you haven't been bitten by clocks. Also: spending tokens on denied requests, or `Retry-After` rounded down.

---

## 5. Go further (optional)

- Stripe engineering blog, "Scaling your API with rate limiters" (Paul Tarjan) — token bucket plus load shedders in production; search the title on stripe.com/blog.
- Redis documentation, "Scripting with Lua" and the `EVAL` atomicity guarantees (redis.io/docs).
- IETF HTTPAPI working group draft on `RateLimit` header fields (draft; status may have changed — check the datatracker).
- DDIA chapter 8 (clocks, monotonic vs time-of-day) for the clock argument.

## Tomorrow
Day 05 — API design: REST vs RPC, idempotency keys, pagination, versioning.
