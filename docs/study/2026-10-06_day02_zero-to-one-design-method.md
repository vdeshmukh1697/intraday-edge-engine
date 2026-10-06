# Day 02 — The 0→1 design method: requirements → v1 monolith + Postgres → sequencing → what you'd refuse to build
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-06*

**Why this matters in a founding-engineer interview** — The 0→1 design round is the one place a founder watches you make scoping decisions live. Candidates trained on big-company loops open with Kafka, sharding and microservices; founders hear "this person will burn our runway on infrastructure for users we don't have." The strongest signal you can send is a boring v1, a clear reason it is boring, and a precise list of the tripwires that would make you change it.

---

## 1. Primer (10 min)

### The mental model: design is a sequence of bets, not a picture

A big-company design answer is a picture of the end state. A 0→1 answer is a **timeline**: what you ship in week 2, what you add only when a specific number crosses a threshold, and what you refuse to build until then. The unit of thought is not "component" but "**decision + the trigger that reverses it**".

### The five-step method (say it out loud at the start of the round)

1. **Clarify the product, not the tech.** Who is the first user, what is the one job they hire this for, what does "working" mean in 2 weeks? Ask about team size, deadline, budget and compliance.
2. **Pin down numbers.** Users, requests/sec, data size, read/write ratio, latency that a *user* would notice. Estimate out loud; most 0→1 products are < 10 QPS average.
3. **Draw v1: the monolith + Postgres.** One deployable, one database, a managed host. Add components only when a requirement forces one.
4. **Sequence.** Order the work by risk and by what unblocks learning: riskiest assumption first, plumbing last.
5. **State the refusals and the tripwires.** "I would not build X until metric Y crosses Z."

### Key vocabulary

- **Tripwire** — a measurable condition that triggers a re-architecture ("p95 > 500 ms for a week", "primary CPU > 60% sustained", "queue depth growing for 10 min").
- **One-way vs two-way door** — a decision that is expensive to reverse (data model, public API, auth model, tenancy model) vs cheap (library, host, queue choice). Spend design time on one-way doors only.
- **Boring technology** — Dan McKinley's "innovation tokens": a startup has maybe three; don't spend them on the database.
- **Walking skeleton** — the thinnest end-to-end slice (UI → API → DB → deploy) running in production in the first days.
- **Modular monolith** — one deployable, with internal module boundaries enforced by convention/lint, so a later extraction is a refactor, not a rewrite.

### The default v1

```
 Browser / mobile
       │  HTTPS
       ▼
 ┌───────────────────────────────┐
 │  Monolith (FastAPI or Next.js)│   one repo, one deploy, one process type
 │  ├─ auth (hosted or simple)   │
 │  ├─ domain modules            │
 │  └─ in-process job runner  ───┼──► (later: separate worker, same code)
 └──────────────┬────────────────┘
                │
        ┌───────▼────────┐        managed Postgres
        │   Postgres     │◄─────  (backups + PITR ON from day 1)
        └────────────────┘
   + object storage (S3) if files, + Sentry/logs, + CI that deploys on merge
```

---

## 2. Deep dive (25 min)

### 2.1 Requirements: the four questions that change the design

Don't ask 20 questions; ask the four that move the architecture:

| Question | Why it moves the design |
|---|---|
| **What is the consistency/correctness cost of being wrong?** (money, compliance, trading vs. a like-count) | Decides transactions, audit trail, idempotency — a *one-way door* |
| **Who are the tenants?** (single-user, B2C, B2B orgs) | Tenancy model (`org_id` on every row vs schema-per-tenant) is the most painful thing to retrofit |
| **What is the latency users feel, and is any of it real-time?** | Real-time pushes you to websockets/SSE; "eventually in seconds" lets you poll |
| **What must be true in 2 weeks vs. 6 months?** | Defines the sequencing and what you can fake |

Then state assumptions explicitly with numbers: "I'll assume 1,000 users in 6 months, 50 requests/sec at peak, 20 GB of data in year one."

### 2.2 Back-of-envelope numbers worth having in your head (estimates)

- One modest managed Postgres instance (4 vCPU, 16 GB) comfortably serves **~1,000–5,000 simple queries/sec** with proper indexes; plenty of real products run on that for years. *(Order-of-magnitude estimate; depends heavily on query shape.)*
- A single FastAPI/uvicorn process handles on the order of **hundreds to a few thousand req/s** for trivial handlers, and far less when each request does several DB round-trips. Run N workers per box.
- 1M requests/day ≈ **12 req/s average**; a 10× peak factor is ~120 req/s. Most seed-stage products never exceed this.
- A managed Postgres + one app box + CDN: roughly **$50–300/month** at this stage *(estimate; varies by provider)*. Infra cost is rounding error against one engineer's salary — your time is the expensive resource.
- Postgres tables stay comfortable into **hundreds of millions of rows** if indexes fit in RAM and you partition/archive append-only data.

### 2.3 Why monolith + Postgres, and what it buys you

- **One deploy, one log stream, one debugger.** With 1–3 engineers, every network boundary you add is a failure mode you must monitor, version and test. Microservices add distributed-systems problems to a team with no spare capacity.
- **Transactions are free.** In a monolith with one DB, "create order + reserve inventory + write audit row" is one `BEGIN…COMMIT`. Split into services and you need sagas/outbox (see Day 6).
- **Postgres is a Swiss-army knife**: relational data, `JSONB` for semi-structured fields, full-text search (`tsvector`), `pgvector` for embeddings, `LISTEN/NOTIFY` for light pub/sub, `SELECT … FOR UPDATE SKIP LOCKED` for a job queue, advisory locks, row-level security for tenancy. Each of these can postpone a separate system (Elasticsearch, a vector DB, Redis, RabbitMQ) by a year or more.
- **Reversibility**: a modular monolith with a clean data model can be split later. A prematurely distributed system is very hard to merge back.

Postgres as a queue, minimal worked example:

```python
# claim one job safely across many workers — no broker needed
CLAIM = """
UPDATE jobs SET status='running', started_at=now()
WHERE id = (
  SELECT id FROM jobs
  WHERE status='queued' AND run_at <= now()
  ORDER BY run_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING id, payload;
"""
```

That handles hundreds of jobs/sec, which is more than most v1s need. Tripwire to move to a real broker: sustained thousands of jobs/sec, fan-out to many independent consumers, or queue churn bloating the table beyond what autovacuum keeps up with.

### 2.4 Sequencing: riskiest assumption first

A founder wants to see that you order work by **learning value**, not by architectural tidiness. A reasonable week-by-week template:

| When | Ship | Why in this order |
|---|---|---|
| Days 1–2 | Walking skeleton: auth, one table, one endpoint, deploy on merge, error tracking, DB backups | Proves the pipeline; every later change ships in minutes |
| Week 1 | The single riskiest user-facing flow, hand-held where needed (manual steps behind a form) | Tests the product hypothesis, not the architecture |
| Week 2 | Second flow + the data model hardening you can't undo (IDs, tenancy, money types) | One-way doors get attention once real shape is known |
| Week 3–4 | Async work, notifications, admin tooling, basic metrics + alerts on the 2–3 numbers that matter | Operational needs appear only after users do |
| Later | Anything triggered by a tripwire | Evidence-driven |

"**Do things that don't scale**" (Paul Graham's essay) is the product-side twin: a human doing step 3 by hand behind an admin page is a legitimate v1, and a legitimate answer.

### 2.5 The refusal list — and tripwires

This is the signature move. Present it as a table; each row has a trigger:

| I would NOT build (yet) | Because | Build when (tripwire) |
|---|---|---|
| Microservices / service mesh | 3 engineers can't operate 6 deploy pipelines | > ~15 engineers stepping on each other in one deploy, or a component with radically different scaling/failure profile |
| Kubernetes | Ops tax with no payoff at 1 box | Need multi-region, bin-packing across many services, or a dedicated infra hire |
| Kafka | Postgres/SKIP LOCKED queue does it | Sustained >5–10k events/s, replay needed by many consumers, or CDC to a warehouse |
| Sharding | Single primary has 10× headroom | Primary writes/disk past ~60–70% of a *maxed-out* instance after indexes, partitioning and read replicas |
| Custom auth | Security liability | Never, for a startup — use a vetted provider/library until enterprise SSO demands more |
| Multi-region | Complexity + data-residency issues | Contractual latency/availability SLO or data residency law |
| A feature-flag platform / ML platform / data lake | No users to segment yet | Specific experiment/pipeline backlog exists |

The *tone* matters: "I'd refuse this *now*, here's the number that changes my mind" sounds like judgment. "I'd never use Kafka" sounds like dogma. And be willing to **spend** complexity where the product's core risk lives (see 2.7).

### 2.6 Worked example: "Design a price-alert service" (0→1)

*Prompt:* users set alerts ("notify me when RELIANCE crosses 2,900"). We have 2 engineers and 6 weeks.

1. **Clarify:** instruments? (assume ~500 NSE symbols), alerts per user? (≤20), users? (assume 5,000 in 6 months → 100k active alerts), latency tolerance? (seconds are fine, minutes are not), channel? (push + email), correctness cost? (a missed alert = trust loss, a duplicate alert = annoyance — I'd bias toward *at-least-once with dedupe*).
2. **Numbers:** 100k alerts is tiny. One market-data feed produces maybe thousands of ticks/sec across the universe; but evaluating 100k alerts per tick naïvely is wasteful.
3. **v1:** one Python process ingests the feed, keeps `{symbol → sorted alerts by trigger price}` in memory (bisect on above/below lists), and on each tick fires only the crossed alerts. Alerts are persisted in Postgres (`alerts(id, user_id, symbol, direction, price, status)`); on restart, reload from the DB. Firing = in one transaction `UPDATE alerts SET status='fired' WHERE id=… AND status='active'` (the row count is your dedupe) + insert into a `notifications` outbox table; a worker sends push/email from the outbox.
4. **Sequence:** feed ingestion + in-memory matcher with a replay-from-file test (days 1–5) → alert CRUD + outbox + email (week 2) → push + reconnect/gap handling (week 3) → hardening.
5. **Refuse:** a streaming framework, a dedicated rules engine, per-user websockets for live prices, multi-region.
6. **Tripwires:** matcher CPU > 70% of a core → shard by symbol across processes; outbox lag > 30 s → add workers; >1M active alerts → move matching state to a partitioned design.

**Where your own experience lands:** here is where you spend complexity on purpose. From the NSE feed: the *feed connection* is the risky component — you've seen reconnect storms (1,426 reconnects → IP block), so v1 includes a reconnect gate and backoff ladder from day 1, and a **gap-handling rule** (after reconnect, re-evaluate alerts against the latest price so a cross during the gap isn't silently missed). That is "boring everywhere, careful at the one place that can kill the product" — exactly the pattern founders want to see. Use it as one sentence of evidence, not a monologue.

### 2.7 When *not* to be boring

Boring is the default, not a religion. Pay for complexity when:
- **The core differentiator is technically hard** (low-latency matching, ML quality, security) — build that carefully.
- **A one-way door is in play**: data model for money (integer minor units, never floats), tenancy, public API contracts, audit logs.
- **A regulatory requirement exists** (compliance/audit trails in fintech): append-only event logs and immutable records are cheap on day 1 and agonising to retrofit.
- **Failure is asymmetric**: losing user data or double-charging is existential; a slow dashboard is not.

### 2.8 How the answer changes with scale

| | 5 users / 2 weeks | 1M users |
|---|---|---|
| Compute | One box/PaaS, one process type | Stateless app tier behind LB, autoscaled; separate worker fleet |
| Data | One Postgres, no replica; backups + PITR | Primary + read replicas, partitioned hot tables, cache for read-heavy paths, then sharding by tenant/user if writes demand it |
| Async | In-process or SKIP LOCKED jobs | Broker/stream (Kafka/SQS), idempotent consumers, DLQs |
| Search/feeds | `ILIKE` / `tsvector` | Dedicated search index; precomputed feeds |
| Ops | Sentry + uptime ping + 1 dashboard | SLOs, on-call, tracing, runbooks, capacity planning |
| People | Everyone deploys everything | Team boundaries → service boundaries (Conway's law) |

Your LinkedIn experience belongs in the right-hand column: "I've operated that world; here's why I *don't* start there" is stronger than either alone.

---

## 3. Exercise (15 min)

**Task: write a one-page design for "a waitlist-to-onboarding tool for a B2B SaaS" using the method — timed.**

Scenario: a seed-stage startup sells analytics to mid-size Indian lenders. Prospects sign up, get manually approved, then ingest a CSV of loan data and see a dashboard. 3 engineers incl. you, 8 weeks to first paying pilot, ~20 pilot customers, up to 5M loan rows each at the high end.

Use a pen or a text file, keep exactly this format:

1. **Assumptions with numbers** (5 lines max — users, rows, QPS, latency, compliance).
2. **v1 diagram** (ASCII, ≤ 8 boxes).
3. **Sequencing**: four 2-week blocks, each naming the riskiest assumption it tests.
4. **Refusal table**: at least 5 rows, each with a *numeric tripwire*.
5. **One place you deliberately spend complexity**, and why (hint: tenancy + customer financial data).

**Done looks like:** every refusal has a measurable tripwire; the tenancy model (e.g. `org_id` on every table + row-level security) is explicitly chosen; backups/PITR and CSV-ingest idempotency (re-uploading the same file must not duplicate loans) are mentioned; and you can read the page aloud in under 3 minutes.

---

## 4. Interview drill (10 min)

**Q1 (0→1). "Design a URL-shortener / expense-tracker / alert system for a startup. You have 2 engineers and a month."**

<details><summary>Model answer</summary>

Start by restating the product job and asking the four high-leverage questions (cost of being wrong, tenancy, real-time needs, what must be true in 2 weeks). State numbers ("assume 10k users, ~50 QPS peak"). Draw one monolith + Postgres + managed hosting + CI deploy-on-merge. Walk through the data model and the single hardest flow in detail (that's where the interviewer will probe). Give a four-block sequence with the riskiest assumption first. Close with a refusal table: no microservices, no Kafka, no k8s, each with a tripwire. Finish with "the one place I'd spend complexity is X because failure there is existential."
</details>

**Q2 (scale). "Okay, it worked. You now have 1M users and Postgres CPU is at 85%. What do you do?"**

<details><summary>Model answer</summary>

Diagnose before architecting: `pg_stat_statements` to find the top queries by total time; check missing indexes, N+1s, and lock contention. Order of interventions, cheapest first: fix queries/indexes → connection pooling (PgBouncer) → cache hot reads (cache-aside, short TTL) → move reporting/analytical reads to a read replica (accepting replication lag; protect read-your-writes paths) → partition large append-only tables → vertical scale (cheap, buys months) → only then shard by tenant/user key, with a resharding plan. Mention that you set the tripwire months earlier (e.g. CPU > 60% sustained), so this is a planned step, not an emergency. Tie in a LinkedIn-style story if you have one: the fix was usually the access pattern, not the hardware.
</details>

**Q3 (judgment). "What would you deliberately NOT build in the first three months, and how do you know you're right?"**

<details><summary>Model answer</summary>

Give 4–5 items (microservices, k8s, Kafka, custom auth, multi-region, admin UI polish) with reasons rooted in team size and traffic, then the key move: each has a *tripwire*, and you review tripwires monthly. Add what you're *not* refusing: backups/PITR, CI/CD, error tracking, and a sane data model — "the cheap things that are expensive to skip." You know you're right when the metric stays below the tripwire; if wrong, the monolith's module boundaries make extraction a refactor.
</details>

**Q4. "Why Postgres? Why not DynamoDB/Mongo for a startup?"**

<details><summary>Model answer</summary>

Because the access patterns aren't known yet, and a relational store with ad-hoc queries, transactions and constraints is the most forgiving when requirements change weekly. Document/key-value stores force you to design around access patterns up front — the thing a pre-PMF team doesn't know. Postgres also covers JSONB, full-text, vector search and queues, delaying extra systems. I'd pick DynamoDB if I had a known, massive, key-based access pattern and a team already fluent in it; I'd revisit at the tripwires for write throughput or storage.
</details>

**Q5. "Your co-founder wants microservices from day one 'so we don't have to rewrite later'. Respond."**

<details><summary>Model answer</summary>

Agree with the goal (avoid a rewrite), disagree with the means. A modular monolith — enforced module boundaries, one schema per module's tables, internal interfaces — gives 80% of the extraction-ability at 10% of the operational cost. Quantify: each service adds a pipeline, monitoring, versioned contracts and network failure modes; with three engineers that's a real fraction of capacity. Offer the trade: we write down the extraction tripwires now, so we're not gambling. That response shows both technical judgment and founder-chemistry skill (disagree, commit to a decision rule).
</details>

**Common mistakes that sink candidates**
1. **Opening with the architecture diagram** before asking about users, numbers or deadlines — signals a big-company reflex.
2. **Refusals without tripwires** (dogma) *or* tripwires without refusals (a lecture on scale). Founders want both: "not now, and here's when."

---

## 5. Go further (optional)

- Dan McKinley, "Choose Boring Technology" (boringtechnology.club) — the innovation-tokens essay.
- Paul Graham, "Do Things That Don't Scale" (paulgraham.com/ds.html).
- *Designing Data-Intensive Applications* (Kleppmann), Ch. 1 — reliability, scalability, maintainability vocabulary for stating requirements.

## Tomorrow
Day 03 — Postgres for startups: indexes, transactions and isolation levels, when Postgres is enough.
