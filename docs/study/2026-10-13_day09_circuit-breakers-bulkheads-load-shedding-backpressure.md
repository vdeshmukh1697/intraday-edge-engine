# Day 09 — Circuit breakers, bulkheads, load shedding, backpressure
*Week 2: Reliability (your essay's theme) · ~60 minutes · 2026-10-13*

**Why this matters in a founding-engineer interview** — Yesterday's retries decide how hard *you* hit a failing dependency; today's patterns decide whether a failing dependency can take *you* down with it. Interviewers use "what happens when X is slow, not down?" to separate people who have only designed happy paths from people who have been paged. At a 5-person company there is no SRE to catch this, so you are expected to know the four mechanisms, when each applies, and which ones you'd refuse to build yet.

## 1. Primer (10 min)

**Mental model.** Systems rarely die from a dependency being *down*; they die from it being *slow*. A slow call holds a thread, socket, connection-pool slot and memory. Queues in front of it grow, latency grows, callers time out and retry, load rises. Everything below is a way to **bound the damage of one slow thing**.

**Vocabulary.**
- **Circuit breaker**: wraps a call; after enough failures it "opens" and fails fast without calling. After a cool-down it goes "half-open", lets a few probes through, and closes on success. States: closed → open → half-open.
- **Bulkhead**: isolate resources (pools, queues, processes) per dependency or per tenant so one cannot consume them all. Named after ship compartments.
- **Load shedding**: deliberately rejecting some work (503/429) when overloaded, to keep serving the rest at acceptable latency.
- **Backpressure**: propagating "slow down" upstream (bounded queues, blocking writes, TCP window, credits) instead of buffering without limit.
- **Admission control**: deciding at the door whether to accept a request (shedding is its overload form).
- **Little's Law**: `L = λ × W` (in-flight = arrival rate × latency). If W goes 50 ms → 5 s at 200 req/s, in-flight goes 10 → 1,000. This one equation explains most slow-dependency outages.
- **Graceful degradation**: serve a reduced answer (cache, default, partial) rather than an error.

```
No protection:                     With bulkhead + breaker + shed:

 requests ─▶ [ shared pool 50 ]     requests ─▶ [admission: queue≤N, deadline check] ──shed──▶ 503
                │  │  │                             │
          ┌─────┘  │  └─────┐                  [pool A: 20]      [pool B: 10]
        svc A    svc B    svc C (slow)           breaker(A)        breaker(C)
                                                    │                 │ open → fail fast / fallback
 C slow → all 50 slots stuck on C                 svc A OK          svc C slow
 → A and B traffic starves too                    (unaffected)
```

## 2. Deep dive (25 min)

### 2.1 Circuit breaker: what it is and isn't
It protects **you** (stops tying up resources on a doomed call) and gives the **dependency** room to recover (stops the load). It does *not* fix correctness.

Design knobs, and the traps in each:
- **Trip condition**: consecutive failures is simple but flaps on low traffic; failure *rate* over a sliding window with a **minimum request volume** (e.g. ≥50% of last 20+ calls) is the standard. Count timeouts as failures; usually don't count 4xx.
- **Open duration**: fixed (e.g. 10–30 s) with jitter, or exponential. Too short = you hammer a recovering service; too long = you stay down after it healed.
- **Half-open**: allow *one or a few* probes, not a flood. Everyone else keeps failing fast.
- **Per-instance vs shared state**: each process has its own breaker (simple, no coordination, slightly slower to react) vs shared via Redis (more machinery, new failure mode). Per-process is the right default.
- **What "fail fast" returns**: a cached value, a default, a queued-for-later acknowledgement, or an honest error. Deciding this is the product work; the breaker is five lines.
- **Retries inside breakers**: the breaker should sit *outside* the retry loop (or count each attempt) so retries don't mask the failure rate.

Minimal implementation (single process, asyncio):

```python
import time, asyncio

class Breaker:
    def __init__(self, window=20, min_calls=10, fail_ratio=0.5, open_for=15.0):
        self.window, self.min_calls = window, min_calls
        self.fail_ratio, self.open_for = fail_ratio, open_for
        self.results: list[bool] = []       # True = success, last `window`
        self.opened_at: float | None = None
        self.probing = False

    def _allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.open_for and not self.probing:
            self.probing = True              # half-open: exactly one probe
            return True
        return False

    def _record(self, ok: bool):
        if self.probing:                     # probe decides the state
            self.probing = False
            if ok: self.opened_at, self.results = None, []
            else:  self.opened_at = time.monotonic()
            return
        self.results = (self.results + [ok])[-self.window:]
        fails = self.results.count(False)
        if len(self.results) >= self.min_calls and fails / len(self.results) >= self.fail_ratio:
            self.opened_at = time.monotonic()

    async def call(self, fn, *a, fallback=None, timeout=2.0):
        if not self._allow():
            if fallback is not None: return fallback()
            raise RuntimeError("circuit open")
        try:
            r = await asyncio.wait_for(fn(*a), timeout)
            self._record(True); return r
        except Exception:
            self._record(False); raise
```
(Simplified: not thread-safe, one global probe. In production use a library — e.g. `pybreaker`, or `resilience4j` on the JVM — but be able to write this on a whiteboard.)

### 2.2 Bulkheads
The cheapest, highest-value pattern for a small team and the one people forget.
- **Separate pools per dependency**: own `httpx.AsyncClient(limits=...)` or semaphore per upstream. `asyncio.Semaphore(20)` around calls to the slow vendor means it can hold at most 20 slots no matter how slow it gets.
- **Separate worker queues per job class** (Day 6): a backlog of 100k "send email" jobs must not delay "process payment" jobs. Two queues, two worker groups.
- **Separate processes/instances for critical vs bulk paths**: public API vs admin/export endpoints; webhook ingestion vs webhook delivery (Day 7).
- **Per-tenant limits**: one noisy customer shouldn't starve others (ties to your Day 4 rate limiter).
- Cost: some idle capacity (pools sized for peak per partition). That's the price; state it.

### 2.3 Load shedding and backpressure
These are the same idea at different layers: *refuse work you cannot finish in time*.

**Unbounded queues are a bug.** A queue converts overload into latency, then into memory exhaustion. Worse, by the time a queued request is served, the client has timed out — you do 100% of the work for 0% of the value ("goodput" collapses while "throughput" looks fine). Rules:
1. **Bound every queue** (`asyncio.Queue(maxsize=N)`). Size it by `N ≈ target_latency × service_rate`, not "big".
2. **Reject at the edge** with 503/429 + `Retry-After` when full. Fast rejection is cheap; slow failure is expensive.
3. **Check the deadline when dequeuing**: if the request's deadline (Day 8) has passed, drop it; don't process it.
4. **LIFO or newest-first under overload** (adaptive LIFO): the oldest requests are the most likely already abandoned. Estimate benefit: meaningful under sustained overload only; don't build it v1.
5. **Prioritise**: shed bulk/background before interactive; shed free tier before paid; never shed health checks or the thing that clears the backlog.
6. **Concurrency limits beat rate limits for protecting yourself**: a rate limit says "≤ 100/s"; a concurrency limit says "≤ 20 in flight", which automatically tightens when latency rises (Little's Law). Adaptive concurrency limiters (Netflix's concurrency-limits, TCP-Vegas-style) exist; a static semaphore gets you 80%.

**Backpressure** is the cooperative version for streams and pipelines: the consumer signals capacity and the producer waits.
- In-process: bounded `asyncio.Queue` → `await q.put()` blocks the producer.
- TCP: window shrinks when the receiver is slow; `await writer.drain()` in asyncio respects it. Ignoring it (writing without draining) is how a slow websocket client eats your RAM.
- Message systems: consumer lag / prefetch limits (RabbitMQ `prefetch_count`), pull-based consumers (Kafka) make the consumer set the pace.
- When you **can't** push back on the producer (a market data feed, a user firehose), you must choose a drop policy explicitly: drop oldest, drop newest, **conflate** (keep latest per key), or spill to disk. Silent unbounded buffering is the wrong answer.

### 2.4 Worked example: your NSE feed
Your tick pipeline is the textbook backpressure case. Ticks arrive from the Dhan websocket at a rate you don't control. If the consumer (indicator calc, DB write, UI broadcast over your own websocket) is slower than the feed:
- **Unbounded queue** → memory climbs, tick-to-decision latency grows to seconds, and you trade on stale prices — in markets, stale data is worse than no data.
- **Better**: bounded queue per symbol with **conflation** (keep only the latest tick per instrument for the UI; keep the full stream for the writer via a separate, larger bounded buffer with an explicit drop/alert policy). Two consumers = two bulkheads: a slow dashboard client must never delay the persistence path.
- **Per-client send queues** on your outbound websocket: cap at N messages; if a client falls behind, drop it or conflate. One slow browser tab shouldn't be able to grow server memory.
- **Connect to Day 8**: your reconnect gate was a *client-side admission control* — it refused to dial. The same instinct, applied to inbound work, is load shedding. "I built the outbound version after an incident; the inbound version is the same shape" is a good sentence.

### 2.5 LinkedIn-scale framing (estimates, speak from experience, not numbers you can't back)
At large-company scale these are infrastructure, not code you write: service meshes enforce timeouts/circuit breaking, request quotas and per-caller limits protect shared services, and overload typically triggers shedding by priority class. Describe *what you observed* (a downstream slowing → upstream thread-pool exhaustion → cascading latency) and what mechanisms contained it. Don't claim you built a mesh feature unless you did.

### 2.6 5 users vs 1M users
| | 5 users, 2 weeks | 1M users |
|---|---|---|
| Timeouts | Explicit on every call (non-negotiable) | Plus deadline propagation across services |
| Breaker | Probably none; a timeout + a fallback for the one flaky vendor | Per-dependency breakers, metrics on state changes |
| Bulkhead | Semaphore per vendor; separate queue for bulk jobs | Separate pools/instances/cells per tier and tenant |
| Shedding | Bounded queue + 503 | Priority classes, adaptive concurrency limits, autoscale-aware |
| Backpressure | Bounded queues everywhere | Credit-based flow control, consumer-lag autoscaling |
| Observability | Log when a breaker opens or a queue sheds | Dashboards, alerts on shed rate, saturation |

**What you would NOT build at seed stage:** a distributed (Redis-shared) circuit breaker, a service mesh, adaptive LIFO, cell-based architecture. **What you would build on day one:** explicit timeouts, a semaphore per external dependency, bounded queues, a 503 path, and a log line when any of them fires. Those are an afternoon, not a project.

### 2.7 Failure modes of the protections themselves
- **Breaker flapping** (open/close every few seconds) → missing min-volume or too-short open time.
- **Breaker hides a bug**: a 400 caused by your own bad request opens the breaker for a healthy dependency. Count only dependency-health errors.
- **Fallback that is itself a dependency** (fallback calls a second flaky service) → you've doubled the blast radius.
- **Shedding with no client cooperation**: clients that retry 503 immediately defeat it. Hence `Retry-After`, client backoff (Day 8), and retry budgets.
- **Over-shedding**: static thresholds tuned for last year's traffic reject healthy load. Alert on shed rate; treat any non-zero in steady state as a signal.
- **Metastable failure**: even with a breaker, if a cache is cold-started during overload the backend can't recover. Mitigation: shed more aggressively while warming, or pre-warm.

## 3. Exercise (15 min)

**Task: "Protect the checkout."** A small e-commerce/fintech app (FastAPI + Postgres) has endpoints: `POST /checkout` (calls payment vendor, p50 300 ms), `GET /recommendations` (calls an ML service, flaky), `POST /exports` (heavy CSV export, 30 s each). The vendor starts taking 20 s per call. Single instance, 40 worker slots.

Do, in 15 minutes, on paper or in a file `protect_checkout.md`:
1. (3 min) Using Little's Law, compute what happens to in-flight requests at 30 req/s when vendor latency goes 0.3 s → 20 s. State the result and what breaks first.
2. (7 min) Write the **protection plan**: for each endpoint, give timeout, bulkhead (pool/semaphore/queue sizes with your reasoning), breaker yes/no and fallback, and shed policy/priority.
3. (5 min) Write the 4-line **alert/log list** you'd add, and one sentence on what you're deliberately *not* building yet.

**Done looks like:** numbers for step 1 (30 × 20 = 600 in-flight vs 40 slots → starvation in ~1.5 s); recommendations and exports cannot consume checkout capacity; `/checkout` has a clear user-facing behaviour when the vendor is degraded (e.g. accept order as "pending", reconcile via Day 6's outbox); at least one explicit "not yet".

## 4. Interview drill (10 min)

**Q1 (0→1). You're the first engineer; your product calls a third-party LLM/payment API that's occasionally slow. What do you add before launch?**
<details><summary>Model answer</summary>
Four cheap things, in order: (1) an explicit total timeout and connect timeout on that client, from observed p99; (2) a semaphore (or dedicated pool) capping concurrent calls to that vendor so it can't consume all workers; (3) a defined degraded behaviour — cached/default response, or accept-and-process-later via a queue, or an honest error — decided with the product in mind; (4) one log/metric when the timeout or cap trips. I would *not* build a circuit breaker library integration or shared state yet; with one flaky dependency and one instance, timeout + cap + fallback gets most of the protection. I'd add a breaker when the dependency is on a hot path and the timeout cost itself (e.g. 2 s × every request) becomes the problem.
</details>

**Q2 (scale). Your service's p99 jumps from 200 ms to 30 s and the pods keep restarting. Walk me through it.**
<details><summary>Model answer</summary>
Hypothesis first: a downstream slowed; Little's Law means in-flight requests ballooned, pools/threads/memory saturated, health checks started failing, the orchestrator restarted pods (which removes capacity and sends the same traffic to fewer pods — a death spiral). Check: dependency latency dashboards, pool saturation, queue depth, retry rate. Immediate mitigation: shed load at the edge, cut retries, disable the slow feature behind a flag, scale only if the bottleneck isn't the dependency. Fix: timeouts and deadlines, bulkheads per dependency, bounded queues with fast rejection, liveness probe that doesn't depend on the downstream (so slowness doesn't trigger restarts), and a breaker with a fallback. Postmortem point: the failure was amplification, not the original slowness.
</details>

**Q3 (judgment — what would you NOT build). Would you put a circuit breaker on every outbound call?**
<details><summary>Model answer</summary>
No. A breaker earns its place when (a) the dependency is on a hot path, (b) failure is correlated and sustained, so fast-failing saves real resources, and (c) there's a meaningful fallback. For a rarely called admin integration, a timeout is enough and a breaker is more state to reason about and a new way to be wrong (flapping, hiding bugs). Bulkheads and timeouts are universal; breakers are selective. I'd instrument first, and add breakers where the data shows long slow-call windows.
</details>

**Q4. Difference between rate limiting, load shedding and backpressure?**
<details><summary>Model answer</summary>
Rate limiting enforces a *policy* on a caller regardless of system health (fairness, cost, abuse). Load shedding is a *reaction* to system overload — reject some work to protect the rest, usually by priority. Backpressure is *cooperation*: the consumer tells the producer to slow down, so nothing is dropped if the producer can comply. Concurrency limits sit between: they self-tighten as latency rises. In practice: rate limit per tenant at the edge, concurrency limit per dependency, bounded queues with shedding at saturation, backpressure inside pipelines you control, and an explicit drop/conflate policy where you can't push back.
</details>

**Q5. A WebSocket server broadcasts market ticks to browsers; some clients are slow. Design for it.**
<details><summary>Model answer</summary>
Per-client bounded outbound queue; the producer never awaits a slow client — it enqueues or applies policy. Policy depends on data semantics: for prices, conflate to latest-per-symbol (the old tick has no value); for trade confirmations, don't drop — disconnect the lagging client and let it resync from a snapshot/sequence number. Use `drain()` / send timeouts, and a server-side metric of queue depth and drops per client. Keep persistence on a separate path so a slow UI can't affect it. This is what I did in my NSE feed system: [be ready to name the specific policy you actually used and any you'd change].
</details>

**Common mistakes that sink candidates**
1. Saying "add a circuit breaker" as a reflex without naming the trip condition, the fallback, or what you do while open — and not mentioning timeouts, which are the prerequisite.
2. Proposing unbounded queues / "just scale up" as the answer to overload. If you can't say what you drop and why, you haven't designed the failure mode.

## 5. Go further (optional)
- Michael Nygard, *Release It!* (2nd ed.) — chapters on Stability Patterns (Circuit Breaker, Bulkheads, Fail Fast) and Stability Antipatterns.
- Google SRE Book, "Addressing Cascading Failures" and "Handling Overload" chapters (sre.google/sre-book).
- Amazon Builders' Library: "Using load shedding to avoid overload" (aws.amazon.com/builders-library) — verify title on site; I could not fetch it in this run.

## Tomorrow
Day 10 — Observability v1: logs, metrics, traces; SLOs and error budgets for a five-person team.
