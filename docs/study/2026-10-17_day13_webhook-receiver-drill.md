# Day 13 — Practical drill: webhook receiver with signature verification, retries and dedupe
*Week 2: Reliability · ~60 minutes (first milestone of a 90-minute drill) · 2026-10-17*

**Why this matters in a founding-engineer interview** — Webhooks are the smallest realistic place where every Week 2 idea shows up at once: untrusted input, at-least-once delivery, retries, idempotency and a "what do we do when we're down" story. A take-home or live-coding round that says "build a webhook receiver" is really testing whether you verify before you trust, ack fast, dedupe correctly under concurrency, and write down what you deliberately skipped. The Day 7 mock was the *sender*; today you build the *receiver* and see the other half of the contract.

## 1. Primer (10 min)

**Mental model: the receiver's job is to turn an unauthenticated, duplicated, reordered, possibly-replayed HTTP POST into exactly one durable fact — and to answer fast.** Everything else is detail.

```
Provider ──POST──▶ [1 read RAW bytes] ─▶ [2 verify HMAC + timestamp] ─▶ [3 dedupe + persist, one tx]
                                                                   │              │
                                                           bad sig: 400/401   dup: 200 (no-op)
                                                                                  ▼
                                                                  [4 return 2xx fast] ─▶ worker processes later
```

**Vocabulary**
- **HMAC signature**: provider computes `HMAC_SHA256(secret, signed_payload)`, sends it in a header. You recompute and compare. Proves the sender knows the shared secret and the body wasn't altered.
- **Raw body**: the exact bytes received. Parsing JSON and re-serialising changes whitespace/key order and breaks the signature. #1 beginner bug.
- **Constant-time compare**: `hmac.compare_digest`, not `==`, to avoid timing leaks.
- **Replay attack**: a valid captured request re-sent later. Defence = signed timestamp + tolerance window (Stripe's default is 5 minutes) and/or dedupe on event ID.
- **Event ID / delivery ID**: the provider's stable identifier. Retries of the same event carry the *same* ID. (Check the provider: some distinguish event ID from per-attempt delivery ID. Using the wrong one either dedupes nothing or drops legitimate events.)
- **Ack-then-process**: persist, return 2xx, do the real work asynchronously. Providers typically time out in seconds (often 5–30s; varies, check docs) and treat non-2xx or timeouts as failure → retry.
- **Dead-letter / failed table**: where events go after you give up, so a human can replay them.

**The contract you must internalise:** providers are at-least-once (Day 6). Your 2xx means "I durably have it", *not* "I finished the business logic". Your non-2xx means "please retry me".

## 2. Deep dive (25 min)

### 2.1 Signature verification: the details that bite

Typical scheme (Stripe-style; GitHub uses `X-Hub-Signature-256: sha256=<hex>` over the body only, no timestamp): header carries `t=<unix>,v1=<hex>`; signed payload is `f"{t}.{raw_body}"`.

```python
import hmac, hashlib, time

class BadSignature(Exception): ...

def verify(raw: bytes, header: str, secrets: list[str], tolerance=300, now=None) -> None:
    parts = dict(p.split("=", 1) for p in header.split(","))
    ts, sig = parts.get("t"), parts.get("v1")
    if not ts or not sig or not ts.isdigit():
        raise BadSignature("malformed")
    if abs((now or time.time()) - int(ts)) > tolerance:
        raise BadSignature("stale")            # replay window
    signed = ts.encode() + b"." + raw
    for secret in secrets:                     # >1 secret = zero-downtime rotation
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig):
            return
    raise BadSignature("mismatch")
```

Failure modes worth naming in an interview:
1. **Verifying parsed JSON** instead of raw bytes. In FastAPI use `await request.body()` *before* anything that parses; don't declare a pydantic body param on this route.
2. **Non-constant-time compare.** Low practical risk over the internet but it's a free signal that you know.
3. **No timestamp check** → a captured request is valid forever. Dedupe on event ID also stops replays, but only for as long as you retain IDs — so retention must exceed the tolerance window and, ideally, the provider's retry horizon.
4. **Secret rotation.** Accept a list of secrets during a rotation window. Without it, rotating the secret = dropped webhooks = incident.
5. **Clock skew.** Your tolerance has to absorb NTP drift; 5 min is generous for that reason. Log "stale" rejections separately — a burst of them usually means *your* clock or a provider retry backlog, not an attack.
6. **Body size / DoS.** Verification happens after reading the body, so cap size at the proxy (e.g. 1 MB; estimate, set from real payloads). Reject unsigned traffic cheaply before touching the DB.
7. **Status codes.** Bad signature → 400/401, and the provider should *not* keep retrying forever (most give up or alert). Never 5xx a bad signature: you'd invite retry storms from your own misconfiguration.

### 2.2 Dedupe: where correctness actually lives

The naive version is a race:

```python
if not seen(event_id):      # two concurrent retries both pass this
    insert(event_id); process()
```

The fix is to make the database arbitrate, in the same transaction as the persist:

```sql
CREATE TABLE webhook_events (
  provider    text        NOT NULL,
  event_id    text        NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  payload     jsonb       NOT NULL,
  status      text        NOT NULL DEFAULT 'pending',   -- pending | done | failed
  attempts    int         NOT NULL DEFAULT 0,
  PRIMARY KEY (provider, event_id)                       -- the dedupe is the constraint
);
-- receive path:
INSERT INTO webhook_events (provider, event_id, payload) VALUES ($1,$2,$3)
ON CONFLICT DO NOTHING;      -- rowcount 0 => duplicate => still return 200
```

Key points:
- **Unique constraint = dedupe.** No application-level lock, no Redis `SETNX` needed at v1. (Redis adds a second system whose state can disagree with Postgres — the Day 6 outbox lesson.)
- **Duplicate returns 200, not 409.** The provider is retrying precisely because it thinks you failed; a 4xx on a dupe can disable your endpoint.
- **Receive ≠ process.** The insert is the ack boundary. A worker claims rows with `SELECT ... WHERE status='pending' FOR UPDATE SKIP LOCKED` (Day 6) and the *business effect* must itself be idempotent (e.g. keyed upsert on the entity, or its own unique key), because the worker can crash after the effect but before marking `done`.
- **Ordering is not guaranteed.** `invoice.paid` may arrive before `invoice.created`. Options: make handlers order-independent (compare entity version/`updated_at`, ignore stale events), or on seeing an unknown entity, fetch current state from the provider API rather than trusting the event payload. "Treat the webhook as a notification, re-fetch as truth" is the most robust v1 pattern.
- **Retention:** keep rows long enough to cover the provider's longest retry horizon plus your replay needs (days to weeks; Stripe retries for up to ~3 days in live mode — verify in current docs). Then partition/prune by `received_at`.

### 2.3 Retries — both directions

*Provider → you* (you don't control it): expect exponential-ish backoff over hours/days. Your obligations: respond 2xx quickly, return 5xx only for *your* transient failures (DB down), and tolerate a **thundering herd on recovery**: after a 20-minute outage, every provider replays its backlog at once. That's Day 9's load shedding: a cheap, bounded receive path (verify → one insert → 200) can absorb a burst that the full handler cannot. If you must shed, return 503 with `Retry-After`, and prefer shedding *before* the DB.

*You → your own handler* (you do control it): worker retries with capped exponential backoff + full jitter (Day 8), `attempts` counter, and a ceiling (say 8 attempts) after which `status='failed'` and an alert fires. A failed row is a **replayable asset**: provide a one-liner CLI/script to reset it to `pending`. Distinguish permanent failures (payload doesn't parse, unknown event type → mark done/ignored, don't retry) from transient ones (downstream timeout → retry). Retrying a poison message forever is the classic queue-clogger.

### 2.4 5 users vs 1M users

| | 5 users / 2 weeks | Heavy traffic |
|---|---|---|
| Ack | Insert row in request, return 200 | Same — it's already cheap. Add connection pooling, batch inserts only if profiled |
| Processing | One in-process asyncio worker polling `pending` | Separate worker pool, `SKIP LOCKED`, or a real queue once polling latency/DB load matters |
| Dedupe | PK on (provider, event_id) | Same; partition the table by time to keep the index hot and pruning cheap |
| Ordering | Re-fetch entity state | Per-entity ordering via partition key if you move to a log (Day 18) |
| Multi-tenant | One secret in env | Per-tenant secrets in a secrets store, endpoint `/hooks/{tenant}`, per-tenant rate limits |
| Ops | Log + a `failed` count on a dashboard | Lag metric (oldest pending age), SLO on ack latency, replay UI |

A realistic throughput note (estimate): a single Postgres instance doing small `INSERT ... ON CONFLICT` on a PK can sustain low thousands of writes/sec on modest hardware, so "Postgres as the inbox" is not the bottleneck for essentially any pre-Series-A company.

### 2.5 Your experience, mapped

- **The NSE feed is the same problem inverted.** You were the *consumer of a retrying/streaming provider* whose misbehaviour (reconnect storms) got you IP-blocked. On the receiver side you are the one that gets stormed. Say: "I've been on both sides of an unbounded retry loop; here's why my receive path is bounded and why dupes get a 200."
- **DhanHQ-py #65 / pykiteconnect #232:** both are "assumption about the environment was silently false" bugs (running event loop; TLS without SNI). Signature verification over parsed vs raw body is the same class — a quiet environmental mismatch that works in the happy-path test and fails in production. Fuel for the essay: *the invalid-signature, duplicate-delivery and out-of-order paths are exactly the code nobody tests.*

## 3. Exercise (15 min) — milestone 1 of the 90-minute drill

**Full 90-minute brief (do over the weekend):** Build a FastAPI webhook receiver for a fictional provider with (a) HMAC-SHA256 + timestamp verification with secret rotation, (b) Postgres/SQLite inbox with PK dedupe, (c) ack-then-process worker with capped, jittered retries and a `failed` state plus replay script, (d) tests for every failure path, (e) a `DECISIONS.md` of ≤ 1 page.

**Today's hour = milestone 1 (15 minutes of typing, after reading):** write `verify()` and the inbox insert — nothing else.

Do this:
1. SQLite is fine (`INSERT OR IGNORE`, check `cursor.rowcount`; same semantics as `ON CONFLICT DO NOTHING`).
2. Implement `receive(raw: bytes, header: str) -> int` returning an HTTP status: 400 bad/stale signature, 200 on new **and** duplicate.
3. Write a `sign(raw, secret, ts)` test helper and these tests (pytest):
   - valid signature → 200 and 1 row
   - same request twice → 200, still 1 row
   - one byte of body altered → 400, 0 rows
   - timestamp 10 minutes old → 400
   - signed with *old* secret while `[new, old]` configured → 200
   - **concurrency:** 20 threads/tasks send the identical event → exactly 1 row

**Done looks like:** all six tests green; `receive` never parses JSON before verification; you can explain in one sentence why the concurrency test would fail against `if not seen(): insert()`. Stretch (if time): 5-line note in `DECISIONS.md` titled "What I deliberately did not build" (queue broker, Redis, per-tenant secrets, UI) with the trigger that would make you build each.

## 4. Interview drill (10 min)

**Q1 (0→1). "You integrate with a payments provider's webhooks. Walk me through v1."**
<details><summary>Model answer</summary>
Clarify: which events matter, what the provider guarantees (at-least-once, no ordering, retry horizon, signing scheme), and my SLO (don't lose a payment event; process within a minute). v1: one FastAPI route that reads raw bytes, verifies HMAC with timestamp tolerance and a secret list, inserts into a Postgres inbox keyed by (provider, event_id) with ON CONFLICT DO NOTHING, and returns 200 — including for duplicates. A worker in the same deployment claims pending rows with SKIP LOCKED, applies an idempotent effect, and marks done; capped jittered retries, then a `failed` state with an alert and a replay script. Handlers re-fetch current entity state from the provider rather than trusting payload order. I'd not build a broker, per-tenant config or a UI yet; the triggers are poll lag or DB load, a second customer with their own secrets, and ops pain from manual replay.
</details>

**Q2 (scale). "The provider has a 30-minute outage and then replays everything at once. What happens to your service?"**
<details><summary>Model answer</summary>
The receive path is the part that must survive: verify (CPU only) → one indexed insert → 200, with no downstream calls, so it's bounded per request. I'd rate-limit/shed at the edge with 503 + Retry-After if the DB saturates, since a well-behaved provider will back off. The backlog lands in the inbox and the worker drains it at its own pace; I'd watch oldest-pending age, not queue depth. Dupes from the replay are absorbed by the PK. If drain time breaches the SLO, scale workers horizontally (SKIP LOCKED) before touching the receive tier, and prioritise by event type if some are time-sensitive. My own reconnect-storm incident taught me that recovery is when systems get hurt, so I test this with a replayed-burst load test.
</details>

**Q3 (judgment). "Should we use a message broker (SQS/Kafka) as the inbox instead of a Postgres table?"**
<details><summary>Model answer</summary>
Not at v1. The inbox needs to be durable, deduped, queryable ("show me everything from customer X last Tuesday") and replayable — a table does all four with the transactional guarantees I already operate. A broker gives better fan-out and consumer scaling but needs its own dedupe, a DLQ, and weaker ad-hoc inspection, and adds a failure mode between "verified" and "stored". I'd switch when sustained write volume or multiple independent consumers make the table the bottleneck, and I'd migrate by keeping the table as the system of record and adding the broker downstream via an outbox. That's a reversible decision, so I'd make the cheap one now.
</details>

**Q4. "Why return 200 for a duplicate rather than 409?"**
<details><summary>Model answer</summary>
Because the provider retries when it believes delivery failed, usually because our earlier ack was lost or slow. From its view, the dup is the same event it still wants acknowledged. A 4xx can count as a failure and, after enough of them, get the endpoint disabled or alerting. Idempotent receivers return the same success they would have the first time.
</details>

**Q5. "A customer says a webhook never arrived. How do you investigate?"**
<details><summary>Model answer</summary>
Look up (provider, event_id) in the inbox: absent → check the provider's delivery log (did they attempt? what status did we return?) and our edge logs for signature rejections or 5xx at that timestamp; present with `pending` → worker lag or a stuck lock; `failed` → read the error, fix, replay via the script; `done` → the downstream effect is the issue. That runbook only works because every state is a row, which is the argument for the inbox table. Afterwards add whichever signal would have caught it earlier (alert on failed > 0 or oldest-pending age).
</details>

**Common mistakes that sink candidates**
- Verifying the signature on re-serialised JSON, or dedupe via check-then-insert, then asserting "it's idempotent." Interviewers often probe concurrency right here.
- Doing the real work inside the request ("it's only a DB write and an email") so provider timeouts create duplicate side effects. Also: returning 5xx for a bad signature or 409 for dupes.

## 5. Go further (optional)
- Stripe docs, "Receive Stripe events in your endpoint" (signature verification, replay tolerance, retry behaviour) — docs.stripe.com/webhooks. Retry schedules are version/mode dependent; check current docs.
- GitHub Docs, "Validating webhook deliveries" (`X-Hub-Signature-256`, constant-time compare).
- Designing Data-Intensive Applications, ch. 11 (stream processing: idempotence, exactly-once effects) and ch. 7 (isolation) for the dedupe race.

## Tomorrow
Day 14 — Review + mock: design a real-time notification system, 0→1 then to 1M users.
