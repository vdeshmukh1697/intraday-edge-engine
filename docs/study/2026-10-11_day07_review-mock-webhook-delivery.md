# Day 07 — Review + Mock: design v1 of a webhook delivery service
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-11*

**Why this matters in a founding-engineer interview** — "Design a webhook delivery service" is a favourite 0→1 prompt because it forces every Week-1 skill at once: scoping, Postgres-as-the-default, API/idempotency design, a queue with honest delivery semantics, and the outbox. Interviewers are scoring whether you ship a defensible v1 in an hour and can say what you would refuse to build yet — not whether you can draw Kafka.

*Format note: every 7th day replaces Deep dive + Exercise with a full mock. Timing: 5 min recall quiz, 35 min mock (set a timer, talk aloud or write), 10 min self-score, 10 min interview drill.*

## 1. Primer (10 min) — Week 1 in one page

| Day | Load-bearing idea to say out loud |
|---|---|
| 1 | Founding engineers are scored on judgment under ambiguity + speed; have a one-breath narrative. |
| 2 | Requirements → v1 monolith + Postgres → sequencing → name what you'd refuse to build. |
| 3 | Postgres is enough far longer than you think; know indexes, isolation, `SKIP LOCKED`. |
| 4 | Rate limiting: token bucket (burst-friendly, O(1)) vs sliding window (precise, costlier); write down decisions. |
| 5 | Idempotency keys, cursor pagination, additive versioning. |
| 6 | At-least-once + idempotent consumer = effectively-once; outbox kills the dual write. |

**Mock-interview protocol.** Spend the first 3–5 minutes on clarifying questions, state assumptions with numbers, draw v1 before any scale talk, and finish with an explicit "not building yet" list. Interviewers at startups reward the person who volunteers the cut list.

### Quick recall quiz (5 questions — answer on paper before opening)

1. Why is "INSERT row, then publish to Kafka" a bug, and what is the fix?
2. A token bucket has capacity 100, refill 10/s. A client is idle for an hour, then sends 500 requests instantly. How many pass?
3. Name two reasons to prefer cursor pagination over `OFFSET`.
4. What does `SELECT ... FOR UPDATE SKIP LOCKED` give a Postgres-backed queue?
5. Under READ COMMITTED, can two transactions both read "balance = 100" and both decrement it? How do you prevent it?

<details><summary>Answers</summary>

1. It's a dual write: the DB commit and the publish are two systems with no shared transaction. A crash between them loses the event (or, in the reverse order, publishes an event for data that never committed). Fix: write the event to an `outbox` table in the *same* transaction as the business row; a relay process reads the outbox and publishes (at-least-once), consumers dedupe.
2. 100 pass immediately (the bucket caps at capacity, so the idle hour doesn't bank 36,000 tokens); the remaining ~400 are rejected or throttled, with ~10/s trickling in afterwards.
3. Offset scans and discards skipped rows (O(offset) cost); and results shift under concurrent inserts/deletes so pages duplicate or skip items. Cursors (keyset on an indexed, unique ordering such as `(created_at, id)`) are O(log n) per page and stable.
4. Multiple workers can claim distinct rows concurrently without blocking on each other's locks — a contention-free work queue with no extra infrastructure. Pair it with a lease/`locked_until` column so crashed workers' jobs get reclaimed.
5. Yes (lost update) in a naive read-then-write. Prevent with an atomic `UPDATE ... SET balance = balance - x WHERE balance >= x`, `SELECT ... FOR UPDATE`, optimistic version column, or SERIALIZABLE with retry.
</details>

## 2. Mock interview — "Design a webhook delivery service"

*(Replaces Deep dive and Exercise today.)*

### The prompt (as the interviewer says it)

> "We're a SaaS platform — think payments or an AI-agent run platform. Customers want to register HTTPS endpoints and get notified when events happen in their account: `invoice.paid`, `run.completed`. Design the service that delivers those webhooks. You're the first backend engineer; we want something running in two weeks, and we'd like to know it won't embarrass us in a year."

### Clarifying questions you should ask (and the assumed answers to use)

| Question | Why it matters | Assumed answer |
|---|---|---|
| How many customers/endpoints and events per day? | Decides Postgres vs. stream. | 200 customers, ~500 endpoints; 1M events/day at peak growth (~12/s avg, ~100/s burst). *Estimate.* |
| Delivery guarantee? Ordering? | Drives the whole design. | At-least-once; best-effort order, not guaranteed. Consumers get an event ID to dedupe. |
| Payload size, latency target? | Storage and timeouts. | <64 KB; p95 first attempt within 5 s of event. |
| Do customers need to verify authenticity? | Signing is non-negotiable for money events. | Yes — HMAC signatures. |
| What happens when a customer endpoint is down for a day? | Retry horizon, disable policy. | Retry for ~3 days, then mark endpoint failing and notify. |
| Self-serve debugging / replay? | Often the real product value. | Yes: delivery log + manual redeliver. |
| Multi-tenant noisy neighbours? | Fairness. | Yes; one slow customer must not delay others. |

State assumptions with numbers. 12/s average is *tiny*; this is the point where you say "one Postgres, a handful of workers."

### Staged model walkthrough

#### Stage 1 — Requirements (2 min, say these aloud)

**Functional:** register/rotate endpoint + secret; subscribe to event types; emit events from the core app; deliver via HTTPS POST with a signature; retry with backoff; log every attempt; manual redeliver.
**Non-functional:** at-least-once; no event lost once the business transaction commits; isolation between tenants; observability of failures; secure against SSRF.

#### Stage 2 — v1 architecture (10 min)

```
 core app txn                    webhook-service (same repo, same DB at v1)
┌──────────────┐   same txn    ┌──────────────────────────────────────┐
│ business row │──────────────▶│ events (id, type, payload, created)  │
└──────────────┘               └─────────────┬────────────────────────┘
                                             │ fan-out step (in-txn or poller)
                                             ▼
                               ┌──────────────────────────────────────┐
                               │ deliveries (id, event_id, endpoint_id│
                               │  status, attempt, next_attempt_at,   │
                               │  locked_until, last_status, last_err)│
                               └─────────────┬────────────────────────┘
                     SELECT … WHERE status='pending' AND next_attempt_at<=now()
                     FOR UPDATE SKIP LOCKED  │
                                             ▼
                               N async workers (httpx, per-request timeout)
                                             │ POST + X-Signature, X-Event-Id
                                             ▼
                                    customer endpoint
```

Key decisions to narrate:

1. **Events table written in the business transaction** — this *is* the outbox. No broker, no dual write.
2. **Fan-out to a `deliveries` row per (event, subscribed endpoint).** One row = one retry state machine. Fan-out can happen in the same transaction (cheap at 500 endpoints) or by a poller.
3. **Postgres as the queue** with `FOR UPDATE SKIP LOCKED` and a lease (`locked_until`). At ~100/s burst this is comfortably within a single modest Postgres (*estimate*: low thousands of claim-updates/s are routine).
4. **Worker loop (sketch):**

```python
async def claim(conn, n=50):
    return await conn.fetch("""
        UPDATE deliveries SET locked_until = now() + interval '30 seconds',
                              attempt = attempt + 1
        WHERE id IN (
          SELECT id FROM deliveries
          WHERE status = 'pending' AND next_attempt_at <= now()
            AND (locked_until IS NULL OR locked_until < now())
          ORDER BY next_attempt_at
          LIMIT $1 FOR UPDATE SKIP LOCKED)
        RETURNING *""", n)

BACKOFF = [10, 60, 300, 1800, 7200, 21600, 43200]  # seconds, + jitter; ~3 days total

async def deliver(d, client):
    body = d.payload_bytes
    ts = int(time.time())
    sig = hmac.new(d.secret, f"{ts}.".encode() + body, "sha256").hexdigest()
    try:
        r = await client.post(d.url, content=body, timeout=10, headers={
            "X-Event-Id": d.event_id, "X-Timestamp": str(ts),
            "X-Signature": f"v1={sig}"})
        ok = 200 <= r.status_code < 300
    except httpx.HTTPError as e:
        ok, err = False, repr(e)
    # update row: succeeded | next_attempt_at = now + BACKOFF[attempt]*jitter | dead
```

5. **Signing:** HMAC-SHA256 over `timestamp.body` with a per-endpoint secret; timestamp lets receivers reject replays; support two active secrets during rotation.
6. **Timeouts and semantics:** 2xx = success; 3xx not followed (or limited); 4xx other than 408/429 → retry sparingly or fail fast; 5xx/timeouts → backoff. Honour `Retry-After`. Cap response-body reads.
7. **SSRF defence** (many candidates forget this): resolve DNS yourself, refuse private/link-local/metadata ranges (10/8, 172.16/12, 192.168/16, 169.254.169.254, loopback), pin the resolved IP for the connection, no redirects into private space. HTTPS only.
8. **Idempotency for consumers:** stable `event_id` in header and body; document "dedupe on it."
9. **API surface:** `POST /endpoints`, `GET /deliveries?cursor=`, `POST /deliveries/{id}/redeliver`, `POST /events/{id}/replay`. Cursor pagination on `(created_at, id)`.

**Why not Kafka/SQS on day one?** 12 events/s avg doesn't need a broker; a broker adds a second failure domain and *still* needs per-delivery retry state, a delay mechanism and a delivery log — which you'd keep in Postgres anyway.

#### Stage 3 — Bottlenecks and failure modes (8 min)

| Problem | What actually happens | Mitigation |
|---|---|---|
| **One slow/dead customer** | Workers all stuck on 10 s timeouts → head-of-line blocking for everyone. | Per-endpoint concurrency cap (e.g. 5 in flight) via claim query/`LATERAL` or a small in-memory semaphore per endpoint; short timeouts. |
| **Retry storm** | Customer comes back after outage; thousands of backlogged retries hit at once and knock it over again. | Jitter on backoff, per-endpoint rate limit (reuse Day 4 token bucket), gradual drain. This is your 1,426-reconnect story in a different costume — the client IP-block is exactly what an unthrottled retrier does to someone else. |
| **Circuit-breaking** | 500 consecutive failures wastes capacity. | Endpoint-level health state: after N consecutive failures, pause to a slow probe cadence; auto-disable after 3 days; email owner. |
| **Worker crash mid-flight** | Request sent, ack not recorded → redelivery. | Lease expiry reclaims it; duplicates are expected, hence `event_id`. |
| **Table growth** | `deliveries` grows ~1M rows/day (*estimate*; ~0.5–1 KB each → hundreds of MB/day). | Partition by day, drop partitions after 30 days retention; keep partial index on `status='pending'` so the claim query stays small. |
| **Polling latency/load** | Tight poll loop hammers DB. | Poll every 200–500 ms with jitter, or `LISTEN/NOTIFY` as a wake-up hint (still poll as source of truth). |
| **Payload secrets/PII** | Logged bodies leak. | Store payload by reference, redact in logs, encrypt endpoint secrets at rest. |

#### Stage 4 — Evolution to 1M users / 100× volume (5 min)

- **~1k events/s:** still plausible on Postgres with partitioned tables and separate worker pools, but the queue's write amplification (every attempt = an UPDATE) starts to show. Split `deliveries` hot state from the log (append-only `attempts` table).
- **Beyond that:** move the *scheduling* to a broker (SQS/Kafka/Redis streams) fed by the outbox relay; keep Postgres as the system of record and delivery log. Shard workers by `endpoint_id` hash for per-endpoint ordering and concurrency control.
- **Ordering:** if customers truly need it, give per-endpoint (or per-resource) sequence numbers and deliver serially per key — which trades throughput and amplifies head-of-line blocking. Most vendors document "no ordering guarantee; use timestamps/versions" instead.
- **Isolation:** priority/tenant-weighted queues, dedicated pools for big customers, regional egress IPs and a published IP allowlist (customers will ask).
- **Product features:** event-type filtering, batching, a customer-facing debug UI, test-fire button, signature libraries in popular languages.

#### Stage 5 — What I would NOT build yet (say this unprompted)

- Kafka/SQS, or a separate microservice with its own DB.
- Exactly-once delivery (impossible across a network boundary; say so).
- Guaranteed ordering.
- Per-tenant custom retry policies, payload transformation, a rules engine.
- Multi-region active-active delivery.
- A custom dashboard UI — start with the API + a CLI/`curl` runbook.

**Where your own experience slots in.** The retry/backoff ladder and the anti-reconnect gate from the NSE feed are the same discipline from the *client* side: you were on the receiving end of a 429 penalty and an IP block for 1,426 reconnects, so you know why jitter, per-target rate limits and circuit state matter. And from your DhanHQ/pykiteconnect work: failure paths (TLS, loop binding, ping loops) are where reliability bugs hide — so you'd test the outage-recovery path of this service explicitly (a chaos test where the endpoint returns 500 for an hour, then recovers). Use these as 20-second asides, not as the answer's spine.

### Self-scoring rubric (10 min — be harsh)

Score each 0 / 1 / 2.

| # | Criterion | 2 = clearly did it |
|---|---|---|
| 1 | Asked ≥4 clarifying questions and put **numbers** on volume | |
| 2 | Drew v1 with Postgres and no broker, and justified it | |
| 3 | Used the outbox / same-transaction event write and named the dual-write bug | |
| 4 | Stated delivery semantics (at-least-once, no exactly-once) and consumer dedupe via event ID | |
| 5 | Covered signing, timestamp/replay protection and SSRF | |
| 6 | Handled slow-customer isolation and retry storms (jitter, per-endpoint limits) | |
| 7 | Gave a concrete evolution path with triggers ("when X, then Y") | |
| 8 | Volunteered a "not building yet" list | |
| 9 | Talked about operability: delivery log, replay, alerts, retention | |
| 10 | Stayed within time, structured out loud, invited feedback | |

**16–20:** hire-signal. **11–15:** solid; repeat in a week, fix the zeros. **<11:** redo the diagram cold tomorrow before Day 8.

## 3. Interview drill (10 min)

Answer aloud first (~90 s each), then compare.

**Q1 (0→1). "You have two weeks and one engineer. What do you ship in week one versus week two?"**
<details><summary>Model answer</summary>

Week one is the thin vertical slice that is correct under failure: events table written in the business transaction, deliveries table, a single worker process with `SKIP LOCKED`, HMAC signing, fixed exponential backoff with jitter, SSRF guard, and an attempts log. Week two adds what makes it operable: manual redeliver/replay API, endpoint auto-disable with owner notification, metrics (delivery success rate, p95 attempt latency, queue age) with an alert on *oldest pending age*, retention job, and docs plus a sample verifier. Explicitly deferred: broker, ordering, custom policies. I'd demo with a deliberately flaky receiver to prove recovery behavior.
</details>

**Q2 (scale). "Traffic goes up 100×, and one customer's endpoint takes 30 s to respond. What breaks first?"**
<details><summary>Model answer</summary>

Head-of-line blocking: the worker pool's concurrency is consumed by slow in-flight requests (Little's law — in-flight = rate × latency, so 30 s responses multiply required concurrency 3× versus 10 s timeouts). Fix by capping per-endpoint concurrency, lowering the timeout (5–10 s is typical; the customer should ack fast and process async), and moving that endpoint to a slow lane after consecutive timeouts. Second bottleneck is the DB: every attempt is an UPDATE, so I'd separate hot queue state from the append-only attempt log, partition by time, and only then consider a broker for scheduling. I'd be measuring queue age and claim-query latency to know *when*, not guessing.
</details>

**Q3 (judgment / NOT build). "Product wants guaranteed in-order, exactly-once webhook delivery. Do you build it?"**
<details><summary>Model answer</summary>

No, and I'd explain why in product terms. Exactly-once is not achievable over an unreliable network — the sender can't distinguish "request lost" from "response lost" — so we provide at-least-once plus a stable event ID that makes consumer-side dedupe trivial. Strict ordering forces serial delivery per key, so one failing event blocks all later ones for that customer; that trades availability for a guarantee most consumers don't need if events carry a version or timestamp and the payload is fetchable via API. I'd offer: "events carry `sequence` per resource; customers can fetch current state on receipt," and revisit per-resource ordered delivery if a design partner demonstrates a concrete need.
</details>

**Q4. "How does a customer know a webhook really came from you, and what if their secret leaks?"**
<details><summary>Model answer</summary>

HMAC-SHA256 over `timestamp + "." + raw body` with a per-endpoint secret, constant-time comparison on their side, and a tolerance window (e.g. 5 minutes) to prevent replay. Secrets are random 32+ bytes, shown once, stored encrypted. Rotation supports two active secrets with an overlap window, and we sign with both (or the new one after overlap) so customers rotate without downtime. If a secret leaks: rotate immediately, and because events are replayable and carry IDs, the customer can audit deliveries from the log. Mention that IP allowlisting is a supplement, not a substitute.
</details>

**Q5 (design smell). "Why not just publish to Kafka from the request handler?"**
<details><summary>Model answer</summary>

Because it's a dual write: if the process dies after the DB commit but before the publish, the event is lost; if I publish first and the commit fails, customers get an event for something that didn't happen. The outbox makes the event durable atomically with the business change. A broker also doesn't remove the need for per-delivery retry state and a delivery log, so at current volume it's extra infrastructure for no capability gain. I'd adopt one later when relay throughput or fan-out justifies it — with the outbox still as the source.
</details>

**Common mistakes that sink candidates**
1. Jumping straight to Kafka + microservices without a volume number, and never mentioning the dual-write problem.
2. Claiming "exactly-once delivery" — instantly signals you haven't operated this class of system. Equally bad: forgetting SSRF and signing, which for a webhook sender are core security requirements.

## 4. Go further (optional)

- Designing Data-Intensive Applications (Kleppmann), Ch. 11 "Stream Processing" — the sections on message delivery, idempotence and fault tolerance; Ch. 8 for why networks make exactly-once impossible.
- Stripe's webhook documentation ("Receive Stripe events in your webhook endpoint" — signatures, retries, best practices) and the Standard Webhooks specification (standardwebhooks.com) for a vendor-neutral signing format. *(Cited by title; not link-verified in this unattended run.)*
- PostgreSQL docs: "Explicit Locking" → row-level locks, `SKIP LOCKED` (postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE).

## Tomorrow

Day 08 — Timeouts, retries, backoff and jitter; retry storms (your 1,426-reconnect incident as the story).
