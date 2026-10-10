# Day 06 — Background jobs and queues: when to add one, at-least-once delivery, idempotent consumers, the outbox pattern
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-10*

**Why this matters in a founding-engineer interview** — Almost every 0→1 design prompt (webhooks, notifications, payouts, agent runs, report generation) has a step where something slow or flaky must leave the request path, and interviewers watch whether you reach for Kafka reflexively or can justify a Postgres-backed queue and explain exactly what "delivered" means. The sharp signal is knowing that exactly-once is a property you build from at-least-once delivery plus idempotent consumers, and that "write to DB, then publish" is a dual-write bug until you use an outbox.

## 1. Primer (10 min)

**Mental model.** A queue is a durable to-do list that decouples *accepting* work from *doing* work. You pay for it with: a second failure domain, delayed results, duplicate execution, and a new thing to monitor. Add one only when the work is (a) slow vs. your request budget, (b) flaky (external API), (c) bursty (smooth the spike), or (d) must survive a process crash.

**Vocabulary.**
- **Producer / consumer (worker)**, **job / message**, **visibility timeout / lease** (a claimed job is hidden for N seconds; if not acked, it reappears), **ack / nack**, **DLQ** (dead-letter queue for jobs that exhausted retries), **poison message** (always fails; blocks or burns retries), **delay/scheduled job**.
- **Delivery semantics:** *at-most-once* (may lose), *at-least-once* (may duplicate), *exactly-once* (effects happen once — achievable only as "at-least-once delivery + idempotent processing" within a system boundary).
- **Dual write:** updating two systems (DB and broker) with no shared transaction. One can succeed while the other fails.
- **Outbox:** write the event to a table in the *same DB transaction* as the business change; a separate relay publishes it.

```
 request path                       async path
┌────────┐  1 tx   ┌──────────────────────┐   2 poll/claim   ┌────────┐
│  API   │────────▶│ Postgres             │◀─────────────────│ worker │
│        │         │  orders  (business)  │  FOR UPDATE      │        │
│        │         │  jobs    (outbox/    │  SKIP LOCKED     │ 3 do   │
└────────┘         │           queue)     │                  │  work  │
                   └──────────────────────┘◀─────────────────│ 4 ack  │
                                              (mark done /   └────────┘
                                               retry w/ backoff / DLQ)
```

**The ladder (what to use when):**
1. Nothing — do it inline if it's <~200 ms and failure can be returned to the caller.
2. `asyncio`/FastAPI `BackgroundTasks` — fire-and-forget in-process. Dies with the process; no retry. Fine for "send analytics ping", never for money or webhooks.
3. **Postgres-backed queue** (a `jobs` table + `SKIP LOCKED`, or a library such as Procrastinate, pgmq, or River in Go — check current maintenance status before betting on one). Transactional with your data. My default through the first ~hundreds of jobs/sec (estimate).
4. Redis-based (RQ, Arq, Celery+Redis, BullMQ) — fast, but durability depends on config (AOF/persistence) and you lose the single-transaction property.
5. Managed broker: SQS (at-least-once standard queues, visibility timeout, built-in DLQ; FIFO queues add dedup within a 5-minute window), Pub/Sub, RabbitMQ.
6. Log-based (Kafka/Redpanda/Kinesis) — when you need replay, many independent consumers, ordering per key, or >~10k msgs/sec sustained (estimate). Operationally heavy for a 3-person team.

## 2. Deep dive (25 min)

### 2.1 Why at-least-once is the only honest default
Worker does the side effect (charge, send email, POST webhook), then crashes before acking. The broker can't know the effect happened, so it redelivers. The alternative (ack first, then work) loses jobs on crash. You can't have both without cooperation from the effect target. Therefore: **assume every job runs 1..N times, possibly concurrently** (a slow worker whose lease expired plus its replacement).

### 2.2 Idempotent consumers — the four techniques
1. **Naturally idempotent operations:** `SET status='paid'` rather than `balance += x`. Upserts with a deterministic key.
2. **Dedup table:** in the *same transaction* as the effect, `INSERT INTO processed(job_id) ... ON CONFLICT DO NOTHING`; if 0 rows inserted, skip. Only works when the effect is in the same DB.
3. **Pass an idempotency key downstream:** for external effects you can't roll back (payments, emails), derive the key from the job (`payout:{id}:attempt-agnostic`) and send it to the provider (Stripe-style `Idempotency-Key`). Link back to Day 5.
4. **Fence with state machine + lease:** `UPDATE jobs SET state='running', lease_until=now()+30s WHERE id=$1 AND state IN ('queued','running') AND lease_until < now()`. Include a monotonically increasing `attempt` / fencing token in the effect so a zombie worker's late write is rejected.

For effects that are not idempotent and have no key (e.g. sending an SMS via a dumb gateway) you must choose: at-most-once (risk silent drop) or at-least-once (risk duplicate). State the choice and the business reason out loud — interviewers love that.

### 2.3 The outbox pattern (the dual-write fix)
Bug: 
```python
async def create_order(...):
    await db.insert_order(...)       # commits
    await broker.publish("order.created", ...)  # crash/timeouts here => order exists, event never sent
```
Reversing the order just flips the failure (event without order). Fix:
```python
async with db.transaction():
    order_id = await db.fetchval("INSERT INTO orders ... RETURNING id", ...)
    await db.execute(
        "INSERT INTO outbox(topic, key, payload) VALUES ($1,$2,$3)",
        "order.created", str(order_id), json.dumps(payload))
# separate relay process:
rows = await db.fetch("""
    SELECT id, topic, key, payload FROM outbox
    WHERE published_at IS NULL
    ORDER BY id LIMIT 100
    FOR UPDATE SKIP LOCKED""")
for r in rows:
    await broker.publish(r.topic, r.key, r.payload)   # may duplicate on crash -> consumers dedupe
await db.execute("UPDATE outbox SET published_at=now() WHERE id = ANY($1)", [r.id for r in rows])
```
Notes: the relay is at-least-once, so consumers must still be idempotent. Ordering by `id` is only approximately commit order (a lower id can commit later than a higher one under concurrency) — if strict per-key order matters, serialize per key or use logical decoding (CDC, e.g. Debezium) instead of polling. **In the small version, the outbox table *is* the queue**: workers claim rows directly and there is no broker at all. That's often the right 0→1 answer.

### 2.4 Postgres as a queue: the claim query
```sql
UPDATE jobs SET state='running', attempts=attempts+1, lease_until=now()+interval '60 seconds'
WHERE id = (
  SELECT id FROM jobs
  WHERE state='queued' AND run_at <= now()
  ORDER BY priority DESC, run_at
  FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```
- `SKIP LOCKED` lets N workers poll without blocking one another.
- Partial index: `CREATE INDEX ON jobs (priority DESC, run_at) WHERE state='queued';`
- Failure modes: table bloat from heavy update/delete churn (tune autovacuum, delete or partition completed jobs); long-running transactions holding back vacuum; polling latency (add `LISTEN/NOTIFY` as a wake-up hint, never as the source of truth — notifications aren't durable); don't hold a DB transaction open for the whole job — claim with a lease, commit, work, then ack.
- Rough ceiling (estimate, hardware- and payload-dependent): low thousands of jobs/sec on a modest instance. Beyond that, or when queue load starts hurting your OLTP queries, move to a dedicated store.

### 2.5 Retries, backoff, DLQ, poison messages
- Exponential backoff with **full jitter** (`sleep = random(0, min(cap, base*2^n))`); this is the same math as your Dhan reconnect ladder — the 1,426-reconnect IP block is what unjittered, ungated retries look like. Cap attempts (e.g. 8–10), then DLQ.
- Classify errors: *retryable* (timeouts, 5xx, 429 — respect `Retry-After`) vs *terminal* (400, validation, 404). Retrying terminal errors is how poison messages burn capacity.
- DLQ needs an owner: an alert when depth > 0 and a replay tool. An unwatched DLQ is silent data loss.
- Per-destination isolation: one dead customer endpoint must not starve everyone (separate queues/concurrency limits per tenant or per destination — preview of Day 9's bulkheads).
- Visibility timeout must exceed p99 job duration, or you get concurrent duplicates; for long jobs, heartbeat to extend the lease.

### 2.6 Ordering, and "5 users vs 1M users"
| | 5 users, ship in 2 weeks | 1M users |
|---|---|---|
| Queue | `jobs` table in Postgres, 2 worker processes | SQS/Kafka, partitioned by tenant/key |
| Ordering | none, or per-entity via state machine | per-partition ordering; hot-key problem |
| Idempotency | unique constraint on `(job_type, natural_key)` | dedup store + downstream idempotency keys |
| Observability | `SELECT count(*), min(run_at) ... WHERE state='queued'` → queue age on a dashboard | queue age SLO, consumer lag, DLQ rate, per-tenant fairness |
| Backpressure | none; maybe a max queue length | rate limits, shed low-priority, autoscale on queue age |

Alert on **age of the oldest queued job**, not depth: depth 10k may be fine, age 20 min on a "send OTP" queue is an incident.

### 2.7 Worked example — webhook delivery (tomorrow's mock, preview)
Event happens → in one tx insert `events` row and one `deliveries` row per subscribed endpoint (`state=queued`, `run_at=now()`). Workers claim, POST with a timeout (e.g. 5–10 s), sign payload, include an `event_id` header so receivers can dedupe. 2xx → done. 5xx/timeout/429 → backoff with jitter up to ~24h–3d, then mark endpoint unhealthy and DLQ. 410/4xx → disable after N. Receivers will see duplicates; that's documented contract, same as Stripe.

### 2.8 Tie to your experience
- **NSE feed:** your reconnect gate is a client-side "queue discipline" — bounded retry rate with a penalty ladder; the incident is the story for why retries need budgets.
- **LinkedIn:** you've seen the Kafka end of the ladder (log-based, replayable, many consumers). The founder-credible move is saying *you know when not to use it*: "At LinkedIn the replay and fan-out justified Kafka; at 3 engineers the operational cost isn't earned until a Postgres queue visibly hurts."
- **DhanHQ-py #65:** blocking calls inside a running event loop — same class of bug as running CPU/blocking work inside a request handler; the fix direction is moving work to a properly owned loop/worker. (Say "submitted patch" unless merge is verified.)

## 3. Exercise (15 min)

**Task: write a minimal Postgres-backed job queue and prove it survives a crash.** Python, `asyncpg` or `psycopg`; if you lack a DB handy, write the SQL + function bodies and trace by hand.

Timebox:
- **5 min** — schema: `jobs(id, type, payload jsonb, state, attempts, max_attempts, run_at, lease_until, last_error, dedupe_key unique nullable, created_at)` plus the partial index. 
- **7 min** — three functions: `enqueue(tx, type, payload, dedupe_key)`, `claim(worker_id)` (the SKIP LOCKED query above), `finish(job, ok, err)` which on failure sets `run_at = now() + full_jitter(attempts)` or `state='dead'` once `attempts >= max_attempts`.
- **3 min** — write the crash trace: worker claims job, performs side effect, is `kill -9`'d before `finish`. Say what happens at lease expiry, what the second worker does, and which technique from §2.2 makes the second run safe.

**Done looks like:** (1) the claim query uses `FOR UPDATE SKIP LOCKED` and a lease, not a long transaction; (2) failure path has jitter and a DLQ state; (3) your crash trace names the duplicate-execution window explicitly and the idempotency mechanism; (4) one sentence on the metric you'd alert on (oldest queued job age).

## 4. Interview drill (10 min)

**Q1 (0→1). "We're a 3-person startup sending payment webhooks and emails. Do we need Kafka/SQS?"**
<details><summary>Model answer</summary>
Not yet. I'd start with a `jobs`/`deliveries` table in the Postgres we already run: enqueue in the same transaction as the business write (which also solves the dual-write problem for free), workers claim with `FOR UPDATE SKIP LOCKED` and a lease, retries use exponential backoff with full jitter and a DLQ state. It handles low thousands of jobs/sec (estimate), costs zero new infrastructure, and one fewer thing to page on. Triggers to migrate: queue churn hurting OLTP latency, need for multi-consumer replay/fan-out, or sustained throughput beyond what one Postgres comfortably shares. I'd keep the enqueue/handler interface narrow so swapping to SQS is a day's work, not a rewrite.
</details>

**Q2 (correctness). "Your worker charges a card, then crashes before acking. What happens, and how do you make it safe?"**
<details><summary>Model answer</summary>
The lease expires, the job is redelivered, and a second worker will charge again unless prevented. Delivery is at-least-once; I make the effect idempotent. I derive a stable idempotency key from the job (e.g. `charge:{order_id}`) and pass it to the payment provider, which returns the original result on replay. I also record the provider's result in the same transaction as the state change, and use a state machine so a job already `succeeded` is a no-op. If a provider doesn't support idempotency keys, I query by my reference before retrying, and for ambiguous timeouts I park the job as `needs_reconciliation` instead of blindly resubmitting.
</details>

**Q3 (scale). "The queue has 2M messages backed up after an outage. What do you do?"**
<details><summary>Model answer</summary>
First check the cause is fixed, otherwise draining just re-fails. Then protect downstreams: scale workers gradually, not to max, so recovery doesn't become a retry storm against a recovering dependency (per-destination concurrency limits and rate limits). Prioritize: process time-sensitive types (OTPs) first or drop expired ones — add TTLs/`expires_at` so stale work is discarded. Watch oldest-message age and error rate while scaling. Afterwards: postmortem on why there was no age alert and no load shedding, and whether the backlog should have been shed by design.
</details>

**Q4 (judgment). "What would you NOT build for background jobs in the first six months?"**
<details><summary>Model answer</summary>
No Kafka, no workflow engine (Temporal etc.) until I have multi-step, long-running, human-in-the-loop processes that I'm hand-rolling badly, no exactly-once claims, no custom scheduler UI, no priority-tiers beyond two. I would build: the transactional enqueue, jitter+DLQ, queue-age alerting, and a replay command. The rule: add machinery when a measured pain appears, but never skip idempotency — that is the cost that can't be retrofitted cheaply.
</details>

**Q5 (outbox). "Why not just publish to the broker after the DB commit?"**
<details><summary>Model answer</summary>
Because those are two systems with no shared transaction: a crash or broker timeout between commit and publish leaves committed state with no event, and the inverse ordering publishes events for rows that never committed. Outbox makes the event part of the same atomic commit; a relay publishes at-least-once and consumers dedupe by event ID. Cost: some extra latency and a relay to run; CDC removes polling if I need stricter ordering or lower latency.
</details>

**Common mistakes that sink candidates.**
1. Saying "exactly-once delivery" as if the broker provides it — or "Kafka transactions solve it" — without discussing the consumer's side effects.
2. Retrying without jitter/caps/error classification, or having a DLQ nobody reads. Also: holding a DB transaction open for the whole job.

## 5. Go further (optional)
- *Designing Data-Intensive Applications*, Kleppmann — Ch. 11 (Stream Processing: message brokers, "exactly-once" and idempotence) and Ch. 7/9 for transactions context.
- Brandur Leach's engineering essays on Postgres job queues and idempotency (brandur.org — search "Transactionally Staged Job Drains" and "Implementing Stripe-like Idempotency Keys"); titles cited from memory, verify before relying on details.
- AWS Architecture Blog, "Exponential Backoff And Jitter" (Marc Brooker).

## Tomorrow
Day 07 — Review + mock: design v1 of a webhook delivery service.
