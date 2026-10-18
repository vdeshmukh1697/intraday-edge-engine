# Day 14 — Review + Mock: real-time notification system, 0→1 then to 1M users
*Week 2: Reliability (your essay's theme) · ~60 minutes · 2026-10-18*

**Why this matters in a founding-engineer interview** — "Design notifications" looks like a CRUD problem and is really a reliability problem: long-lived connections, retries against third parties (APNs/FCM/email providers), fan-out, dedupe and "what happens when the socket silently dies". Week 2 was exactly this toolkit; the interviewer is checking that you reach for it unprompted and still ship a boring v1.

*Format note: every 7th day replaces Deep dive + Exercise with a full mock. Timing: 5 min recall quiz, 35 min mock (timer on, talk aloud or write), 10 min self-score, 10 min interview drill.*

## 1. Primer (10 min) — Week 2 in one page

| Day | Load-bearing idea to say out loud |
|---|---|
| 8 | Every retry needs a timeout, a cap, exponential backoff **with jitter**, and a per-target budget; unthrottled retriers become the outage (your 1,426-reconnect IP block). |
| 9 | Breakers stop calling the dead; bulkheads stop one dependency eating all capacity; shed load at the edge on purpose; backpressure means bounded queues. |
| 10 | Logs for debugging, metrics for alerting, traces for latency; for five people: 2–3 SLOs, alert on symptoms (oldest-pending age, error rate), not causes. |
| 11 | TCP says "connected" long after the peer is gone: heartbeat both ways, deadline-based liveness, jittered reconnect, and **backfill the gap by sequence/cursor** on reconnect. |
| 12 | Incident command: mitigate first, one commander, a timeline, a blameless postmortem with owned action items. |
| 13 | Webhook receiver: verify signature on raw bytes, constant-time compare, timestamp window, dedupe on event ID, ack fast and process async. |

**Mock protocol.** 3–5 min of clarifying questions with numbers, v1 drawn before any scale talk, an explicit "not building yet" list. Today's twist: the failure modes (dead sockets, provider outages, duplicate delivery) should show up in *v1*, not as a late "and then we add reliability" slide.

### Quick recall quiz (5 questions — answer on paper before opening)

1. 10,000 clients lose a connection at the same instant and reconnect with `sleep(2**n)` and no jitter. What does the server see, and what is the fix?
2. A websocket shows `ESTABLISHED` but the NAT dropped the mapping 10 minutes ago. How does each side find out, and how fast?
3. Difference between a circuit breaker and a bulkhead, in one sentence each.
4. After a reconnect, how do you make sure the client missed nothing — and what must the server have kept for that to work?
5. A webhook receiver gets the same `event_id` twice, 30 s apart. What should it do, and why can't the sender prevent this?

<details><summary>Answers</summary>

1. Synchronised waves: everyone retries at t+1, t+2, t+4… so the server sees a thundering herd at each step, possibly knocking over what just recovered. Fix: full or decorrelated jitter (`sleep(random(0, min(cap, base*2**n)))`), plus server-side admission control (`Retry-After`, connection-rate limit) and a client-side reconnect gate.
2. Neither side is told; TCP only notices when it tries to send and gets no ACK (minutes with default retransmit timers) or via keepalive (OS defaults are ~2 h on Linux, *estimate/config-dependent*). So you need application-level heartbeats (ping/pong or data-arrival deadline, e.g. ping every 20–30 s, declare dead after 2–3 missed) — that bounds detection to ~a minute. Also: some libraries' ping loops silently don't ping (your pykiteconnect #237 forensics) — test the liveness path.
3. Breaker: stop sending requests to a dependency that is failing so it can recover and callers fail fast. Bulkhead: partition resources (pools/concurrency) per dependency or tenant so one slow one can't consume them all.
4. Every message gets a monotonically increasing sequence number (per user/stream); the client remembers the last one it processed and sends it on reconnect; the server must retain a replay window (or be able to query durable storage) from that cursor. If the gap exceeds retention, return "resync required" and have the client refetch state.
5. Ignore the second (or re-ack it without reprocessing) using a dedupe table keyed by `event_id`, ideally with the dedupe write in the same transaction as the side effect. The sender can't prevent it: a timeout after the receiver processed but before the ack arrived is indistinguishable from the receiver never getting it, so a correct sender retries — at-least-once is the only honest guarantee.
</details>

## 2. Mock interview — "Design a real-time notification system"

*(Replaces Deep dive and Exercise today.)*

### The prompt (as the interviewer says it)

> "We're building a collaborative product — think a project tool or an AI-agent platform where long-running jobs finish and teammates comment. We want users to get notified in real time: in-app immediately, push notifications to their phones, and email as a fallback. You're the first backend hire. Design it so we can ship in three weeks, and tell me how it holds up if we get to a million users."

### Clarifying questions you should ask (and the assumed answers to use)

| Question | Why it matters | Assumed answer |
|---|---|---|
| Who triggers notifications — system events, user actions, both? How many types? | Drives schema and templates. | Both; ~15 types at launch (mention, assignment, job finished, comment reply). |
| Scale now and in a year? Peak fan-out per event? | Decides Postgres vs. stream, and whether fan-out is a problem. | 5k MAU now; 1M MAU in 18 months. Typical event → 1–20 recipients; a "post to whole workspace" event can be 10k. |
| What does "real time" mean — latency target? | Websocket vs. polling. | In-app p95 < 2 s when online; push within ~10 s; email can be minutes. |
| Delivery guarantee? Is losing one acceptable? | Core semantics. | Never lose an in-app notification (it's the inbox); push/email are best-effort with retries; duplicates must not be annoying. |
| Per-user preferences, mute, quiet hours, digesting? | Often the real complexity. | Per-type/channel preferences at v1; digest later. |
| Read/unread state synced across devices? | Forces a durable inbox. | Yes — unread badge must agree across web and mobile. |
| Which channels, whose providers? | External dependencies = failure domains. | WebSocket, FCM/APNs, one email provider (e.g. SES/Postmark). |
| Multi-tenant? Notification content sensitive? | Isolation, privacy of payloads on lock screens. | B2B workspaces; push payloads carry minimal text, details fetched on open. |

State the numbers: 1M MAU ≈ perhaps 100k–200k concurrent connections at peak (*estimate: 10–20% concurrency*) and a few thousand notifications/s at peak after fan-out (*estimate*). That is a real system but not an exotic one.

### Staged model walkthrough

#### Stage 1 — Requirements (2 min, say these aloud)

**Functional:** emit notification events from the app; per-user durable inbox with read/unread; real-time push to connected clients; mobile push and email when the user is offline or after a delay; user preferences; mark read/all-read.
**Non-functional:** inbox never loses a notification once the business transaction commits; online delivery p95 < 2 s; push/email at-least-once with dedupe; one slow provider can't stall the others; operable by a small team (queue age, provider error rate visible).

**Core design stance:** *the inbox table is the source of truth; real-time is an optimisation layered on top.* If every socket and every push provider disappeared, users would still see correct notifications on next load. That sentence makes the whole reliability story simple.

#### Stage 2 — v1 architecture (10–12 min)

```
 app txn (comment created)
┌─────────────────────────┐ same txn ┌────────────────────────────────┐
│ business row            │────────▶│ notification_events (outbox)   │
└─────────────────────────┘          └───────────────┬────────────────┘
                                                     │ worker: fan-out, prefs, dedupe_key
                                                     ▼
                             ┌─────────────────────────────────────────┐
                             │ notifications (user_id, seq, type,      │
                             │  payload, created_at, read_at,          │
                             │  dedupe_key UNIQUE)   ← the inbox       │
                             └───────┬──────────────────┬──────────────┘
              NOTIFY / in-proc pub   │                  │ channel_deliveries (push, email)
                                     ▼                  ▼   SKIP LOCKED workers, backoff+jitter
                           ws gateway (FastAPI)      FCM / APNs / email provider
                           client keeps last_seq     (breaker per provider)
```

Key decisions to narrate:

1. **Outbox write in the business transaction** (Day 6). No "save, then publish" dual write.
2. **Fan-out worker** reads events, resolves recipients and preferences, and inserts one `notifications` row per user with a per-user increasing `seq` and a `UNIQUE(user_id, dedupe_key)` so a retried fan-out can't double-insert. Idempotent consumer, Day 6.
3. **Inbox API is plain REST**: `GET /notifications?cursor=` (keyset on `(user_id, seq desc)`), `POST /notifications/{id}/read`, `GET /unread_count`. Cursor pagination, Day 5. This alone is a working product.
4. **Real-time layer is a hint, not the data path.** A websocket gateway (FastAPI + asyncio) authenticates, registers the connection in an in-process map `user_id → sockets`, and pushes new rows. At v1 a single gateway process is fine; use Postgres `LISTEN/NOTIFY` (or in-process publish if the fan-out worker runs in the same process) as the wake-up signal. Payload of NOTIFY is just `user_id, seq`; the gateway reads the row.
5. **Reconnect and gap backfill (Day 11):** the client sends `last_seq` on connect; the gateway replays `seq > last_seq` from the table, then switches to live. No separate replay buffer is needed — Postgres *is* the buffer. Heartbeat ping every ~25 s, close after two missed pongs, client reconnects with jittered backoff (cap ~30 s) and a reconnect gate.
6. **Push and email as channel deliveries:** a `channel_deliveries` row per (notification, channel) with `status, attempt, next_attempt_at`. Workers claim with `SKIP LOCKED`. *Policy:* send mobile push only if the user has no active socket (presence check) or has not read it within N seconds (e.g. 30–60 s delay), and collapse with a `collapse_key` so five comments become one push. Email only after a longer delay and only if still unread.
7. **Retries against providers (Days 8–9):** timeout 5–10 s, exponential backoff with jitter, honour `Retry-After`/429, treat FCM/APNs "unregistered token" as permanent failure and delete the token, and keep a circuit breaker per provider so an APNs outage doesn't pin your workers.
8. **Dedupe on the client:** each notification carries a stable ID; the UI keys by ID, so a replay overlapping live delivery doesn't show doubles.
9. **Auth on sockets:** short-lived token in the first message or a subprotocol header (not a query string that lands in logs); authorise by `user_id` on every subscription; never accept a client-supplied `user_id`.

A compact sketch of the gateway's connect handler shows the ordering that prevents loss — *subscribe first, then backfill, then dedupe by seq*:

```python
async def ws_notifications(ws: WebSocket, user=Depends(auth_ws)):
    await ws.accept()
    last_seq = int(ws.query_params.get("last_seq", 0))
    q = hub.subscribe(user.id)               # 1. subscribe BEFORE reading history
    try:
        for row in await db.fetch_since(user.id, last_seq):   # 2. backfill gap
            await ws.send_json(row); last_seq = row["seq"]
        while True:                           # 3. live, skipping anything already sent
            row = await asyncio.wait_for(q.get(), timeout=25)
            if row["seq"] > last_seq:
                await ws.send_json(row); last_seq = row["seq"]
    except asyncio.TimeoutError:
        ...                                   # send ping; close if no pong by deadline
    finally:
        hub.unsubscribe(user.id, q)
```

If you backfill before subscribing, anything inserted between the query and the subscription is lost — the classic race, and exactly the sort of gap your NSE-feed backfill work had to close.

**Why not Kafka/Redis/SNS on day one?** At 5k MAU you have single-digit events/s. A broker adds a failure domain and still needs a durable per-user inbox, read state and retry bookkeeping — all of which live in Postgres anyway.

#### Stage 3 — Bottlenecks and failure modes (8 min)

| Problem | What actually happens | Mitigation |
|---|---|---|
| **Silent dead sockets** | Phone enters a tunnel; server still thinks it's connected, writes go into the kernel buffer. | App-level ping/pong with deadline; bounded per-socket send queue; drop and close slow consumers (backpressure, Day 9). |
| **Reconnect storm** | Gateway deploy or LB blip disconnects 100k clients; all reconnect at once and re-run backfill queries. | Jittered client backoff; server-side connection-rate limiter; drain gradually on deploy (close sockets in batches with `Retry-After`-style hints); backfill queries are indexed range scans, capped per page. |
| **Large fan-out** | "Notify whole workspace" = 10k inserts in one event. | Chunked fan-out jobs (e.g. 500 recipients per job) so one event doesn't hold a long transaction; per-event fan-out cap; digest for noisy types. |
| **Provider outage / slow provider** | FCM 5xx for 20 min; retries pile up. | Breaker per provider, bounded retry horizon (push loses value fast — drop after ~1 h, email after ~24 h), alert on oldest-pending age per channel. |
| **Duplicate notifications** | Retry after timeout; fan-out job re-run. | `UNIQUE(user_id, dedupe_key)`, `collapse_key` for push, client dedupe by ID. |
| **Hot rows** | `unread_count` updated on every insert/read. | Compute count with a partial index on `(user_id) WHERE read_at IS NULL`, cache briefly; denormalised counter only when measured to be needed. |
| **Table growth** | Inbox grows ~tens of millions of rows/month at scale (*estimate*). | Partition by time or hash on `user_id`; retain 90 days online; archive. Backfill beyond retention returns "resync". |
| **Privacy** | Lock-screen shows sensitive text. | Minimal payload in push; fetch on open; user setting for previews. |

#### Stage 4 — Evolution to 1M users (5 min)

Give triggers, not a wish list:

- **Trigger: >1 gateway instance** (~10–50k sockets per box is comfortable with asyncio if messages are small — *estimate; measure*). Now an event produced on node A must reach a socket on node B. Add a pub/sub fabric (Redis pub/sub or NATS) for the wake-up signal; the DB remains the replay source. Route by consistent hash of `user_id` if you want fewer cross-node hops, but don't require it for correctness.
- **Trigger: sustained fan-out >~1–2k inserts/s or the outbox relay lagging:** move fan-out scheduling to a stream (Kafka/Redis Streams/SQS) keyed by `user_id` or `recipient shard`; the outbox relay publishes, consumers insert idempotently.
- **Trigger: inbox table size/IO:** shard by `user_id` (hash), keep `seq` per user so the cursor design survives; hot tenants get dedicated shards.
- **Trigger: providers rate-limit you:** per-provider token buckets (Day 4), priority lanes (security alerts > comments > digests), batching/digests.
- **Trigger: deploy pain:** connection draining, sticky graceful shutdown, a connection-edge tier separated from business logic so deploys of the app don't drop sockets.
- **SSE/long-polling fallback** for corporate proxies that break websockets; mobile OS push is the true "offline" channel.
- **Multi-region:** gateways per region near users, inbox in the primary; accept slightly higher replay latency before building multi-leader anything.

#### Stage 5 — What I would NOT build yet (say this unprompted)

- A separate notification microservice with its own datastore, or Kafka, before gateway count or fan-out forces it.
- Exactly-once delivery or global ordering across users (per-user `seq` is enough).
- A rules/workflow engine, A/B-tested send-time optimisation, ML relevance ranking.
- Rich digests, in-app preference centre with 40 toggles, SMS/Slack/Teams channels — one at a time, after the inbox is trusted.
- Multi-region active-active.

**Where your own experience slots in (20-second asides, not the spine).** The websocket gateway section is the NSE feed turned inside out: you've lived the heartbeat-that-never-fires bug (pykiteconnect #237), the reconnect storm that got a client IP-blocked (1,426 reconnects) and the discipline of an anti-reconnect gate and 429 penalty ladder. At LinkedIn scale you've presumably seen what a fan-out hot key does to a downstream; say which one you have actually seen rather than inventing one. And the essay line applies directly: test the outage path — a chaos test where FCM returns 500 for an hour and the gateway is restarted mid-fan-out.

### Self-scoring rubric (10 min — be harsh)

Score each 0 / 1 / 2.

| # | Criterion | 2 = clearly did it |
|---|---|---|
| 1 | Asked ≥4 clarifying questions, put numbers on MAU/concurrency/fan-out | |
| 2 | Declared the durable inbox the source of truth; real-time is an optimisation | |
| 3 | Drew v1 on Postgres with no broker and justified it | |
| 4 | Used the outbox and idempotent fan-out (`dedupe_key`), named at-least-once | |
| 5 | Covered heartbeats, dead-socket detection and reconnect with jitter | |
| 6 | Explained gap backfill by per-user `seq`, including the subscribe-before-backfill ordering | |
| 7 | Handled provider failure: backoff, breaker, permanent vs. transient errors, retry horizon | |
| 8 | Evolution path with explicit triggers (multi-gateway pub/sub, stream, sharding) | |
| 9 | Volunteered a "not building yet" list | |
| 10 | Operability and timing: queue age, socket count, delivery success SLOs; finished in time | |

**16–20:** hire-signal. **11–15:** solid; redraw the diagram cold tomorrow and fix the zeros. **<11:** redo the mock before reading Day 15.

## 3. Interview drill (10 min)

Answer aloud first (~90 s each), then compare.

**Q1 (0→1). "You have three weeks. What ships in week one versus week three?"**
<details><summary>Model answer</summary>

Week one: outbox event → fan-out worker → `notifications` inbox table with per-user `seq` and dedupe key, REST inbox API with cursor pagination and mark-read, and unread count. That is already a shippable product (users refresh/poll every 30 s). Week two: websocket gateway with heartbeat, auth, `last_seq` backfill; client dedupe by ID. Week three: mobile push via FCM/APNs with presence-aware delay and `collapse_key`, email fallback, user preferences, metrics and an alert on oldest-pending age per channel. Deferred: broker, digests, extra channels, ordering guarantees. I'd demo by killing the gateway and a fake provider mid-run to show nothing is lost.
</details>

**Q2 (scale). "You now have 200k concurrent websockets across several gateway nodes and a deploy just disconnected all of them. What happens and what do you do about it?"**
<details><summary>Model answer</summary>

Without protection: 200k simultaneous reconnects, each triggering auth plus a backfill query — a self-inflicted thundering herd that also overloads the DB and the load balancer. Mitigations, in order: jittered client backoff with a cap; a server-side connection-rate limiter that responds with a retry hint; drain nodes gradually on deploy (close sockets in batches, rolling instance replacement) so reconnects spread over minutes; make backfill cheap (indexed range on `(user_id, seq)`, page-capped, return "resync" if the gap is huge); keep auth stateless (signed tokens) so it doesn't hit the DB. And measure: connection churn rate and backfill query latency are the dashboards I'd want. This is precisely the failure that got my own client IP-blocked in the market-data feed — mine was one client, this is 200k of them, same mechanism.
</details>

**Q3 (judgment / NOT build). "Product wants guaranteed exactly-once, globally ordered delivery of every notification to every device. Do you build it?"**
<details><summary>Model answer</summary>

No, and I'd explain what we give instead. Exactly-once across a network is not achievable; a timeout is ambiguous. What users actually need is "never lose it, never show it twice": a durable inbox with at-least-once delivery plus ID-based dedupe on the client gives that effect. Global ordering is expensive (a single sequencer) and unnecessary; per-user sequence numbers give a stable order in each inbox, which is all anyone can perceive. I'd tell product the cost of the stronger guarantee — latency, a sequencing bottleneck, complexity — and offer to revisit if a concrete user-visible bug appears that the per-user ordering doesn't solve.
</details>

**Q4. "Push notifications work for most users but a segment never receives them. How do you debug it?"**
<details><summary>Model answer</summary>

Start from data, not guesses: slice delivery success by platform, app version, provider response code and token age. Common causes: stale/unregistered tokens not being pruned (provider says `UNREGISTERED`/`BadDeviceToken` and we keep trying), APNs sandbox vs. production environment mismatch, expired credentials on one provider, OS-level notification permission off, or my presence logic suppressing pushes because a zombie socket looks "online" — which is a Day 11 liveness bug. Trace one affected user end to end via the delivery log (event → notification → channel_delivery → provider response). Then fix the class: prune on permanent errors, alert on provider error-rate by code, and add a synthetic canary device that receives a push every few minutes.
</details>

**Q5. "How would you test this system?"**
<details><summary>Model answer</summary>

Unit/property tests for fan-out idempotency (run the same event twice, assert one inbox row) and backfill ordering (inject inserts between the history query and the subscription). Integration tests with a fake provider that returns 500s, 429s with `Retry-After`, and timeouts. A chaos script: kill a gateway mid-stream, partition a client with `tc`/a proxy that drops packets (no FIN), assert the client reconnects, backfills from `last_seq` and shows no gaps or duplicates. A load test of reconnect storms. The failure paths are the product here, so they get the test budget.
</details>

**Common mistakes that sink candidates**

1. **Treating the websocket as the delivery mechanism.** If the socket is the only path, every disconnect loses data. Anchor on the durable inbox and say so in the first five minutes.
2. **Jumping to Kafka and microservices at 5k users**, or conversely never giving triggers for when the simple design stops working. Both read as missing judgment.
3. **Forgetting duplicates and ordering at the edges** — no dedupe key, no `seq`, no subscribe-before-backfill — and never mentioning dead-socket detection.

## 4. Go further (optional)

- *Designing Data-Intensive Applications*, Kleppmann — Ch. 8 "The Trouble with Distributed Systems" (unreliable networks, timeouts) and Ch. 11 "Stream Processing" (delivery guarantees, idempotence).
- AWS Architecture Blog — "Exponential Backoff And Jitter" (Marc Brooker) for the jitter variants referenced above.
- RFC 6455 (The WebSocket Protocol), §5.5.2–5.5.3 on Ping/Pong control frames.

## Tomorrow

Day 15 — Caching: cache-aside, invalidation, stampedes, TTL strategy (Week 3: Data at scale).
