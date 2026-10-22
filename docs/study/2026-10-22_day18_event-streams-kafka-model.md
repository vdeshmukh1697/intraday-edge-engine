# Day 18 — Event streams: the Kafka model, ordering, consumer groups, the exactly-once myth
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-22*

**Why this matters in a founding-engineer interview** — Kafka was built at LinkedIn, so an interviewer who sees it on your résumé will probe harder than usual, and "we'd put it on Kafka" is the most reflexive (and most often wrong) answer in a 0→1 design. They want to hear that you know what a log actually guarantees (per-partition order, replay), where ordering and "exactly once" break down, and — the senior signal — when Postgres plus a jobs table is the better call.

## 1. Primer (10 min)

A **log** is an append-only, ordered sequence of records. A **topic** is a named log split into **partitions**; each partition is an independent log on one broker (leader) with replicas elsewhere. Consumers don't "pop" messages: they track an **offset** (position) per partition and read forward. Reading doesn't delete — retention (time/size) does. That one design choice gives replay, multiple independent readers, and cheap fan-out.

```
 producer ─ key=user_7 ─▶ hash(key) % P ─▶ ┌ partition 0: [0][1][2][3]...  ─▶ consumer A (group G)
                                           ├ partition 1: [0][1][2]...     ─▶ consumer B (group G)
                                           └ partition 2: [0][1][2][3][4]  ─▶ consumer B (group G)
                                                         ▲
                              group H (separate offsets) ┘ reads same data independently
```

Vocabulary:
- **Key** — decides partition; same key → same partition → ordered. No key → round-robin/sticky, no order.
- **Consumer group** — consumers sharing a group id split partitions; each partition is assigned to *at most one* consumer in the group. Parallelism ceiling = partition count.
- **Committed offset** — "I've processed up to here." Stored by the broker (`__consumer_offsets`). Commit timing defines your delivery semantics.
- **Rebalance** — partition reassignment when members join/leave/stall. Historically stop-the-world; cooperative rebalancing and static membership reduce it.
- **ISR / `acks`** — in-sync replicas; `acks=all` + `min.insync.replicas=2` (RF=3) is the durable-write setting.
- **Compacted topic** — keeps the latest record per key; a changelog/table, not a queue.
- **Lag** — log-end offset minus committed offset. The one metric that matters.

Queue vs log: a queue (SQS, a Postgres jobs table) is about *work distribution and completion* — each message processed once, then gone. A log is about *an ordered record many readers consume at their own pace*. Day 6's job queue is the former; today is the latter.

## 2. Deep dive (25 min)

### Ordering — what's actually guaranteed
Total order **within a partition only**. Across partitions: none. So "ordered per entity" means choosing the entity as the key (`account_id`, `instrument_token`). Ways ordering silently breaks:
1. **Producer retries with >1 in-flight request** — batch 1 fails, batch 2 lands, batch 1 retried → reordered. Fix: `enable.idempotence=true` (default in recent clients; it allows up to 5 in-flight while preserving order — verify against your client version).
2. **Changing partition count** — `hash(key) % P` changes, so a key's history is split across two partitions and new events can overtake old ones. Over-provision partitions early (estimate: a few × expected peak consumers) rather than resizing.
3. **Parallelism inside a consumer** — if you poll a batch and hand records to a thread pool/`asyncio.gather`, you've thrown away the order you paid for. Parallelise *across keys*, serially within a key.
4. **Hot keys** — one key = one partition = one consumer. A single whale tenant caps your throughput; Day 17's hotspot problem returns.

### Delivery semantics = when you commit
```python
# At-least-once (the default you should assume): process, THEN commit.
async for msg in consumer:
    await handle(msg)            # may run twice after a crash between these lines
    await consumer.commit()

# At-most-once: commit THEN process. Crash = message lost. Rarely what you want.
```
Crash between handle and commit → redelivery → **handler must be idempotent** (Day 6/13: dedupe key, upsert, `INSERT ... ON CONFLICT DO NOTHING`). A rebalance mid-processing causes the same effect: a slow consumer is kicked out (`max.poll.interval.ms`), its partitions go elsewhere, and the batch is processed twice.

### The exactly-once myth
Kafka's "exactly-once semantics" (idempotent producer + transactions + `read_committed`) is real but narrow: it guarantees *read-process-write within Kafka* (consume topic A, write topic B, commit offsets, all atomically). It does **not** make your email send, your HTTP call or your Postgres write happen once. Once a side effect leaves Kafka, you're back to at-least-once delivery + idempotent effect = "effectively once." Say it that way. The pattern for DB writes: store the offset in the same transaction as the result (`UPDATE ... ; UPDATE consumer_offsets ...`) and seek to it on startup — this is genuinely exactly-once into Postgres, at the cost of owning the offset.

### Failure modes worth naming
- **Poison message** — a record that always throws blocks its partition forever. Needs retry-with-limit then a **dead-letter topic**; never an infinite retry loop (Day 8).
- **Consumer lag spiral** — slow downstream → lag → rebalances from missed polls → more reprocessing → slower. Decouple polling from processing, or pause/resume partitions (backpressure, Day 9).
- **Rebalance storms** — same shape as your reconnect-storm incident: a fleet that all reacts to the same event at once. Static membership, cooperative assignor, sane session timeouts, and jittered restarts are the same medicine.
- **Dual write** — "write to DB then publish to Kafka" loses events on crash. Use the **outbox pattern** (Day 6) with a relay or CDC (e.g. Debezium).
- **Retention vs replay** — you can only replay what you kept. Decide retention (7 days is a common default; estimate, tune to your recovery window) deliberately.

### Numbers worth knowing (estimates)
- A modest 3-broker cluster sustains hundreds of MB/s sequential throughput; a single partition is more like tens of MB/s. Throughput is rarely the issue at startup scale.
- End-to-end latency is typically single-digit to tens of ms with `acks=all` in-region.
- Ops cost is the real number: a self-run cluster needs someone who understands ISR, rebalances and disk. Managed (MSK, Confluent Cloud, Redpanda Cloud) runs from low hundreds to thousands of USD/month (estimate). Postgres you already run costs ~nothing extra.
- LinkedIn's scale (public figures from their engineering blog: trillions of messages/day, circa the late 2010s) is the reason Kafka exists — it's not your Series A.

### 5 users vs 1M users
| | Ship in 2 weeks | 1M users |
|---|---|---|
| Fan-out to other systems | `jobs` table + `LISTEN/NOTIFY` or polling with `SKIP LOCKED` | Kafka topic per domain event, outbox/CDC |
| Ordering | `ORDER BY id` per entity | Key by entity, partitions sized for growth |
| Replay | Re-run from the table | Retention + new consumer group |
| Consumers | One worker | Groups per downstream (search, analytics, notifications) |

**Rule of thumb for adopting Kafka:** you need it when (a) multiple independent consumers need the same ordered stream, (b) you need replay as a feature, or (c) volume exceeds what one Postgres can ingest comfortably. Otherwise it's operational debt with a résumé benefit.

### Worked example — your NSE feed as a log
Ticks arrive per `instrument_token`. Key by token → per-instrument order preserved, parallelism across instruments. Consumers: a candle aggregator, a persistence writer, an alerting service — each its own group, each at its own pace, any one replayable after a bug. The reconnect/gap-backfill logic from Day 11 maps onto offsets: after reconnect you resume from "last offset I processed" rather than guessing from timestamps. And your anti-storm gate is exactly the discipline a consumer fleet needs around rebalances. Honest caveat for the interview: for a solo system at that volume, SQLite/Postgres plus an in-process asyncio queue was the right call, and saying so is a plus.

## 3. Exercise (15 min)

**Task: write an idempotent, order-preserving consumer loop and a one-page Kafka-or-not memo.** (Pen/keyboard, timed.)
1. (8 min) In Python (pseudocode with `aiokafka`-style API is fine), write a consumer that: processes records serially per key but concurrently across keys; commits only offsets up to the lowest *contiguous* completed offset per partition; routes a record to a DLQ after 3 failures with backoff; writes results with `ON CONFLICT (event_id) DO NOTHING`.
2. (7 min) Write 8 lines for this memo: "Product has an order-events pipeline; search, email and analytics all want it. 4 engineers, ~200 events/s peak." Decide Kafka vs Postgres outbox + workers. State the trigger metric that would make you revisit.

**Done =** the loop has an explicit answer to "what happens if the process dies after handle() but before commit?" (redelivery, dedupe makes it safe), and the memo names a concrete, measurable revisit trigger (e.g. "outbox relay lag p99 > 5 s or >5k events/s sustained").

## 4. Interview drill (10 min)

**Q1 (0→1). "We're building an order-processing product with search, email and analytics consumers. Do we need Kafka?"**
<details><summary>Model answer</summary>

At ~200 events/s with four engineers, probably not on day one. I'd write events to an `outbox` table in the same transaction as the order, run a relay that fans out to consumers via a jobs table (`FOR UPDATE SKIP LOCKED`), and keep each consumer idempotent on `event_id`. That gives transactional consistency, replay (re-enqueue from the table) and zero new infrastructure. The design keeps the event contract clean — versioned schema, entity id, monotonically increasing sequence — so moving the relay's output to Kafka later is a swap of transport, not a rewrite. I'd revisit when relay lag exceeds an agreed SLO, when we have many independent consumers needing long replay windows, or sustained throughput outgrows a single Postgres.
</details>

**Q2 (scale). "Your consumer group has 12 partitions and lag is growing. 40 consumers are deployed. What's wrong and what do you do?"**
<details><summary>Model answer</summary>

Only 12 consumers can be active; the other 28 are idle — parallelism is capped by partition count. First check whether the lag is uniform or on one partition (hot key/skew) and whether the bottleneck is the handler (downstream latency, DB) rather than consuming. Options: speed up the handler (batching, bulk inserts), process across keys concurrently within a consumer while keeping per-key order, or increase partitions — which I'd treat carefully because it remaps keys and can break per-key ordering during the transition (drain, or migrate via a new topic). If one key dominates, partitioning can't fix it; I'd split the key (e.g. tenant + sub-shard) if the ordering requirement allows, or accept it.
</details>

**Q3 (judgment). "Does Kafka give exactly-once delivery?"**
<details><summary>Model answer</summary>

Within Kafka, yes, narrowly: idempotent producers plus transactions plus `read_committed` make consume-transform-produce atomic. It doesn't cover external side effects — a payment call or email can still run twice after a crash between effect and offset commit. So I design for at-least-once delivery with idempotent handlers (dedupe key or upsert), and where the sink is Postgres I can store the offset in the same transaction, which is true exactly-once into that DB. Effectively-once is the honest term.
</details>

**Q4 (failure). "One bad message is blocking a partition. Walk me through it."**
<details><summary>Model answer</summary>

Order within a partition is exactly why one failing record blocks the rest: we can't commit past it. Mitigate: bounded retries with backoff and jitter for transient errors, then publish to a dead-letter topic with the original payload, headers, error and offset, commit past it, and alert on DLQ depth. Separate permanent errors (schema/validation) — which go straight to DLQ — from transient ones. Then fix the bug and replay the DLQ. The trade-off: skipping a record breaks strict per-key ordering for that key, so for ledgers I'd instead halt that key and page someone.
</details>

**Q5 (judgment, LinkedIn). "You worked near Kafka at LinkedIn. What would you not carry over to a seed-stage company?"**
<details><summary>Model answer</summary>

The platform. At LinkedIn Kafka had a dedicated team, schema registry, tooling, quotas and monitoring; that investment is what made it safe. A five-person startup has none of that, so the cost-benefit flips. What I'd carry over is the *thinking*: events as an immutable log, consumers owning their own offsets, schemas as contracts, idempotent handlers. I can adopt those on Postgres now and graduate to a managed Kafka when the triggers fire. (Keep claims about specific LinkedIn internals to what you personally worked on.)
</details>

**Common mistakes that sink candidates**
- Saying "exactly-once" without qualifying scope, or claiming global ordering across partitions.
- Reaching for Kafka reflexively with no throughput numbers, and never mentioning the operational cost or the Postgres alternative.

## 5. Go further (optional)
- *Designing Data-Intensive Applications*, Ch. 11 "Stream Processing" (log-based brokers, delivery guarantees) and Ch. 12 on "The Future of Data Systems" for end-to-end idempotence.
- Jay Kreps, "The Log: What every software engineer should know about real-time data's unifying abstraction" (LinkedIn Engineering blog, 2013).
- Apache Kafka documentation, "Design" section (message delivery semantics, replication).

## Tomorrow
Day 19 — Workshop: turn three LinkedIn projects into quantified scale war stories.
