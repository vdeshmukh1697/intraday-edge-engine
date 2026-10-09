# Day 05 — API design: REST vs RPC, idempotency keys, pagination, versioning
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-09*

**Why this matters in a founding-engineer interview** — The API is the first contract you ship that you cannot take back: once a customer's cron job or mobile build depends on it, every mistake becomes permanent support load. Interviewers use "design the API for X" to see whether you think about retries, partial failure and evolution — the things that separate someone who has operated a system from someone who has only drawn one. Idempotency and pagination in particular are cheap on day 1 and brutal to retrofit.

---

## 1. Primer (10 min)

### The mental model

An API is a **contract under unreliable delivery**. The client will retry (timeouts, flaky mobile networks, impatient users double-clicking), the network will duplicate and reorder, and your server will change under the client's feet. Four decisions follow from that:

1. **Style** — resource-oriented (REST-ish) vs procedure-oriented (RPC) vs query-oriented (GraphQL).
2. **Safe retries** — idempotency.
3. **Listing** — pagination that survives concurrent writes.
4. **Evolution** — versioning and compatibility rules.

### Vocabulary

| Term | Meaning |
|---|---|
| Safe method | Doesn't change state (GET, HEAD). Caches/crawlers may call freely. |
| Idempotent | Applying the request N times has the same effect as once. PUT, DELETE are by definition; POST is not unless you make it so. |
| Idempotency key | Client-generated unique token per logical operation; server stores the result and replays it on retry. |
| Offset pagination | `?limit=20&offset=40`. Simple; O(offset) scans; skips/duplicates rows under writes. |
| Keyset (cursor) pagination | `WHERE (created_at, id) < (:c, :i) ORDER BY created_at DESC, id DESC LIMIT n`. Stable and index-friendly. |
| Opaque cursor | Token the client must not parse; lets you change the internals. |
| Additive change | New optional field / endpoint / enum-tolerant value. Non-breaking if clients ignore unknowns. |
| Tolerant reader | Client ignores fields it doesn't know. Postel's law applied to evolution. |

### REST vs RPC vs GraphQL at a glance

```
            REST (resources)         RPC (verbs)              GraphQL
shape       POST /payments           POST /payments:refund    POST /graphql {query}
            GET  /payments/{id}      POST /SendEmail          one endpoint, client picks fields
caching     HTTP caches work (GET)   usually none             hard (POST), needs persisted queries
tooling     curl, browser, OpenAPI   gRPC codegen, strong types  schema + codegen, N+1 risk
best for    public/3rd-party APIs    internal service↔service  many client shapes, BFF
```

The honest founder answer: **"REST-ish JSON over HTTP for anything a customer touches; plain function calls inside the monolith; reach for gRPC/GraphQL only when a concrete pain (multiple clients with divergent needs, internal polyglot services) shows up."** Most real APIs are REST resources plus a handful of RPC-style action endpoints (`POST /orders/{id}/cancel`). That's fine — don't be dogmatic.

---

## 2. Deep dive (25 min)

### 2.1 Resource modelling and the "action" escape hatch

Model nouns that have identity and lifecycle: `/payments`, `/subscriptions`, `/agents/{id}/runs`. For state transitions that aren't a field update, use a sub-resource action: `POST /runs/{id}/cancel`. Reasons: a `PATCH {status: "cancelled"}` hides side effects (refunds, notifications) and invites invalid transitions; an explicit action endpoint can validate, be idempotent by design, and be logged/permissioned separately.

Rules that cost nothing on day 1:
- IDs: opaque, prefixed strings (`run_01HX…`, Stripe-style) — self-describing in logs, impossible to mistake across types, and not enumerable if you use ULID/UUIDv7-ish random components. Don't expose auto-increment integers publicly (leaks volume, invites scraping).
- Money as integer minor units + currency code (`{"amount": 49900, "currency": "INR"}`). Never floats.
- Timestamps ISO-8601 UTC. Enums as strings.
- Consistent error envelope: `{"error": {"code": "rate_limited", "message": "...", "request_id": "req_…"}}`. Machine-readable `code`, human `message`. Always return a request ID — it's your support lifeline.
- Status codes that clients can act on: 400/422 (don't retry), 401/403, 404, 409 (conflict/duplicate), 429 (retry after `Retry-After`), 5xx (retry with backoff). A client's retry policy is derived from your status codes; get them right. (Day 4's limiter used this.)

### 2.2 Idempotency keys — the single most valuable API idea

**Problem.** `POST /payments` times out at the client. Did it execute? The client can't know. Retrying risks a double charge; not retrying risks a lost payment. Naïve "check if exists" isn't possible because the client has no ID yet.

**Solution.** Client sends `Idempotency-Key: <uuid>` with each logical operation. Server records the key with the request fingerprint and the response. Same key + same body → replay stored response. Same key + *different* body → 422/409 (client bug). Same key while the first is still running → 409 (or wait).

State machine for a key row:

```
          INSERT (key, scope, req_hash, status='in_progress', locked_until)
 new ───────────────────────────────────────────────▶ in_progress
                                                          │ handler commits
                                                          ▼
 retry within TTL ◀────────── replay stored response ─ completed(response_code, body)
```

The crux is making **"claim the key" and "do the work" atomic enough**. In a Postgres-backed monolith the cleanest version puts both in one transaction:

```python
# FastAPI + asyncpg sketch. Table:
# idempotency_keys(key text, tenant_id text, req_hash bytea, status text,
#                  response_code int, response_body jsonb, created_at timestamptz,
#                  PRIMARY KEY (tenant_id, key))
import hashlib, json

async def create_payment(conn, tenant_id, key, body: dict):
    req_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).digest()
    async with conn.transaction():
        row = await conn.fetchrow(
            """INSERT INTO idempotency_keys (key, tenant_id, req_hash, status)
               VALUES ($1,$2,$3,'in_progress')
               ON CONFLICT (tenant_id, key) DO NOTHING
               RETURNING key""", key, tenant_id, req_hash)
        if row is None:  # someone already claimed it
            prev = await conn.fetchrow(
                "SELECT req_hash,status,response_code,response_body "
                "FROM idempotency_keys WHERE tenant_id=$1 AND key=$2 FOR UPDATE",
                tenant_id, key)  # blocks until the first txn commits/rolls back
            if prev["req_hash"] != req_hash:
                raise Conflict("idempotency key reused with different body")
            if prev["status"] == "completed":
                return prev["response_code"], prev["response_body"]
            # else: first attempt rolled back -> fall through and redo

        result = await do_payment(conn, tenant_id, body)   # same txn: DB effects atomic with the key
        await conn.execute(
            "UPDATE idempotency_keys SET status='completed', response_code=200, "
            "response_body=$3 WHERE tenant_id=$1 AND key=$2",
            tenant_id, key, json.dumps(result))
        return 200, result
```

Why this works: the key insert, the business writes, and the stored response commit together or not at all (ties directly to Day 3 transaction semantics). The unique constraint arbitrates concurrent duplicates; the `FOR UPDATE` read makes the loser wait for the winner.

**Where it breaks:** the moment `do_payment` calls an *external* system (a PSP, an email provider). You can't roll that back. Then you need (a) pass the same idempotency key downstream (Stripe and most PSPs accept one), and (b) a recoverable `in_progress` state with a lease (`locked_until`) so a crashed worker's key can be resumed rather than stuck forever. That's the same shape as the outbox pattern coming up on Day 6.

Details worth saying out loud:
- Scope keys per tenant/API key so one customer can't collide with or probe another.
- TTL: Stripe documents 24h retention; pick something like that (estimate: retries beyond a day are almost always new intents) and run a cleanup job.
- Hash the *request*, not just the key — it catches the "reused key" bug.
- Store the response even for 4xx you consider deterministic; do **not** store 5xx caused by transient infra, or the client is stuck replaying an error.
- Naturally idempotent designs beat keys where possible: `PUT /resources/{client_chosen_id}`, or a unique constraint on a business key (`UNIQUE(order_id)` on a payment).

**Your experience.** The NSE feed is the consumer-side mirror of this: websocket reconnects *redeliver* ticks, so downstream handlers must be idempotent by `(instrument, exchange_timestamp, seq)`. And the 1,426-reconnect incident is what un-bounded client retries look like from the receiving end — which is why the server tells clients how to retry (`Retry-After`, 429 ladders) rather than leaving it to their imagination. Use this as a one-line bridge: "I've been on both sides of at-least-once."

### 2.3 Pagination

| Approach | Cost at depth | Under concurrent inserts | Random page jump | Use when |
|---|---|---|---|---|
| Offset/limit | O(offset): DB scans and discards rows; page 10,000 of 20/page ≈ 200k rows skipped (estimate; planner-dependent) | Rows shift → duplicates and skips | Yes | Admin tables < ~10⁵ rows |
| Keyset/cursor | O(page) with a composite index | Stable | No | Anything user-facing, feeds, logs, public API |
| Snapshot/ID-range export | O(page) | Frozen | n/a | Bulk export, backfills |

Keyset requirements: a **total order** (sort column + unique tiebreaker, i.e. `(created_at, id)`), a matching composite index, and an **opaque cursor** (base64 of the last tuple, optionally signed) so you can later change the sort internals. Response shape: `{"data": [...], "next_cursor": "…", "has_more": true}`. Never return `total_count` by default — `COUNT(*)` over a big filtered table is a scan; offer it only on explicit request or as an estimate.

```sql
-- index: (tenant_id, created_at DESC, id DESC)
SELECT * FROM runs
WHERE tenant_id = $1
  AND (created_at, id) < ($2, $3)      -- row-value comparison; uses the index
ORDER BY created_at DESC, id DESC
LIMIT 21;                              -- fetch n+1 to compute has_more
```

Trap: sorting by a mutable column (e.g. `updated_at`) makes items jump between pages as they change. Sort by immutable keys for stable traversal; for "recently updated" feeds accept that and document it, or use a change-log/sequence number.

### 2.4 Versioning and evolution

Spectrum: URL (`/v1/`), header/date-based (Stripe's `Stripe-Version: 2024-06-20`, pinned per account), or no explicit version with strict compatibility rules.

The thing that actually keeps you out of trouble is a **compatibility policy**, not the mechanism:
- **Non-breaking (do freely):** add optional request fields, add response fields, add endpoints, add enum values *only if you documented that clients must tolerate unknown values*.
- **Breaking (needs a new version or a migration window):** remove/rename fields, change types or semantics, tighten validation, change default ordering/pagination, change error codes clients branch on.
- Expand → migrate → contract: ship the new field alongside the old, move callers, only then remove.

Scale split:
- **5 users, 2 weeks:** `/v1/` prefix, JSON, API keys, idempotency on money/side-effect POSTs, keyset pagination on any list that will grow, error envelope, request IDs, OpenAPI generated free by FastAPI. Your customers are design partners you can Slack; breaking changes are a message, not a migration.
- **1M users / thousands of integrators:** per-account pinned versions with a compatibility-transform layer (Stripe model), changelog + deprecation headers (`Deprecation`, `Sunset` — IETF RFCs 9745/8594; check current status), SDKs generated from the spec, contract tests against recorded traffic, per-key rate limits (Day 4), webhooks with signed payloads, and a staff-owned API review for every new endpoint.

### 2.5 Worked example: "API for an agent-run tracing product" (his target domain)

```
POST /v1/traces                       (Idempotency-Key optional; trace_id client-supplied => naturally idempotent via PUT-like upsert)
PUT  /v1/traces/{trace_id}/spans/{span_id}   # client IDs => retry-safe by construction
GET  /v1/traces?agent_id=&status=&cursor=    # keyset on (started_at, trace_id)
POST /v1/traces/{trace_id}/replay            # RPC-style action; returns 202 + run_id
GET  /v1/runs/{run_id}                       # poll; later add webhook
```

Notice the decisions: ingestion uses client-generated span IDs so SDK retries are free; the high-volume path is batched (`POST /v1/spans:batch` returning per-item results with 207-style semantics) because one-request-per-span costs ~10–50× more connection/auth overhead (estimate); replay is async (202 + poll) because it's long-running. Each is a sentence you can defend.

---

## 3. Exercise (15 min)

**Task: write the API contract for a "payout" feature and the idempotency table that backs it.** Pen/keyboard, no code execution required.

Scenario: your fintech startup lets merchants trigger `POST /v1/payouts` to send money to a bank account via an external bank API that is slow (2–30 s) and sometimes times out.

Timebox:
- **5 min** — endpoints and shapes: create, get, list (keyset), cancel. Request/response JSON for create, including money, ID format, status enum (`pending`, `processing`, `paid`, `failed`, `cancelled`).
- **5 min** — idempotency: schema of the key table, what the handler does on (a) first call, (b) retry after client timeout while still processing, (c) retry after completion, (d) same key different body, (e) your server crashes after calling the bank but before committing. State what you pass to the bank.
- **5 min** — write 6 bullets for `DECISIONS.md`: what you deliberately don't build yet (e.g., no per-account API versions, no GraphQL, no webhooks on day 1 — or yes webhooks, justify).

**Done looks like:** one page where case (e) has a concrete answer ("payout row is `processing` with a lease; a reconciler re-queries the bank by the same idempotency key / reference; never re-submits blindly"), and the list endpoint states its sort key, tiebreaker and index.

---

## 4. Interview drill (10 min)

**Q1 (0→1). "Design the public API for a payments/payout product for a seed-stage startup."**
<details><summary>Model answer</summary>
Start with the consumers: a handful of integrators writing server-side code, so REST-ish JSON with `/v1/`, API-key auth, OpenAPI from day one. Nouns: `payouts`, `beneficiaries`; actions: `POST /payouts/{id}/cancel`. Money as integer minor units + currency, prefixed opaque IDs, uniform error envelope with request ID. The non-negotiable is `Idempotency-Key` on `POST /payouts`, backed by a Postgres table keyed on `(tenant, key)` with request hash and stored response, updated in the same transaction as the payout row; downstream bank call carries the same key and a reconciler resolves ambiguous timeouts. Lists use keyset pagination on `(created_at, id)`. I'd not build per-account versioning, GraphQL or SDKs yet; I'd add a deprecation policy in the docs so later changes are predictable.
</details>

**Q2 (scale). "Your list endpoint with offset pagination is timing out for big customers. Fix it without breaking clients."**
<details><summary>Model answer</summary>
Diagnose: deep offsets force the DB to scan and discard rows, and concurrent inserts cause duplicates/skips. Fix: add a composite index matching `(tenant_id, created_at DESC, id DESC)` and introduce keyset pagination via an opaque `cursor`. Compatibility: keep `offset` working but cap it (e.g. reject offset > 10,000 with a 400 pointing to cursors), return `next_cursor` in the existing response shape (additive), and announce a sunset date for offset. Remove `total_count` or make it opt-in/approximate. Measure with p95 latency per page depth before and after.
</details>

**Q3 (reliability). "A client retried a POST and the customer was charged twice. Walk me through how that happens and how you prevent it."**
<details><summary>Model answer</summary>
The request succeeded server-side but the response was lost (timeout, dropped connection, load-balancer reset); the client retried and the server treated it as new. Prevention: client-generated idempotency key, server claims it via a unique constraint, stores request hash + response, replays on duplicates, rejects mismatched bodies, and propagates the key to the downstream processor. Also handle concurrent duplicates (second request waits or gets 409) and crash-in-the-middle via an `in_progress` lease plus reconciliation. Where possible, add a business-level unique constraint as a second safety net.
</details>

**Q4 (judgment — what would you NOT build). "Should we use GraphQL for our API?"**
<details><summary>Model answer</summary>
Not at the start. Our consumers are a few integrators and our own web app; GraphQL's benefit is many divergent clients overfetching on rich graphs, and its costs — query-cost limiting, N+1 resolvers, no HTTP caching, harder rate limiting and auth per field — are real for a tiny team. REST + OpenAPI gives codegen, curl-ability and trivial rate limiting. I'd revisit when we have multiple first-party clients with conflicting data needs, and then consider a BFF layer rather than rewriting the public API.
</details>

**Q5 (evolution). "You named a field badly and 50 customers use it. What now?"**
<details><summary>Model answer</summary>
Expand/migrate/contract: add the correctly named field alongside, populate both, document the old as deprecated with a `Sunset` date, notify affected keys (you know who reads it from request/response logs), then remove after the window. If the cost of keeping the bad name is low, don't rename — a bad name that works costs less than a breaking change. Decide by support cost, not aesthetics.
</details>

**Common mistakes that sink candidates**
- Describing idempotency as "check if it already exists first" — a race, and impossible when the client has no ID yet. Say unique constraint + stored response + request hash.
- Proposing microservices, GraphQL, per-version routing and an API gateway for a 2-person team. It signals you design for the résumé, not the company.

---

## 5. Go further (optional)
- Stripe Docs — "Idempotent requests" and "Versioning" (stripe.com/docs/api/idempotent_requests, stripe.com/docs/api/versioning) — the reference design for both topics. (Not fetched in this run; verify the paths.)
- Brandur Leach, "Implementing Stripe-like Idempotency Keys in Postgres" (brandur.org) — the transactional approach used in the sketch above.
- Markus Winand, "Use The Index, Luke" — chapter on "Paging Through Results" (keyset/seek method), use-the-index-luke.com.

## Tomorrow
Day 06 — Background jobs and queues: when to add one, at-least-once delivery, idempotent consumers, the outbox pattern.
