# Day 11 — Long-lived connections: heartbeats, liveness detection, reconnect, gap backfill
*Week 2: Reliability · ~60 minutes · 2026-10-15*

**Why this matters in a founding-engineer interview** — Almost every 0→1 product eventually holds a connection open (chat, notifications, market data, agent streaming, webhooks-in-reverse), and "it works on my laptop" dies the first time a NAT, load balancer or mobile network silently drops a socket. This is also the topic where your NSE feed work is not a story but evidence: you've seen half-open sockets, reconnect storms and gap recovery in production, which most candidates have only read about.

## 1. Primer (10 min)

**Core mental model: a TCP connection can be dead and nobody told you.** TCP only tells you about a failure when you *try to send* and get no ACK, or when a FIN/RST arrives. If the peer crashes, a NAT table entry expires, or a Wi-Fi link drops, your socket still looks `ESTABLISHED` and `recv()` just blocks forever. That is a **half-open connection**. Therefore liveness must be detected at the application layer, with a deadline.

**Vocabulary**
- **Heartbeat / ping-pong**: periodic small message proving the path works both ways. WebSocket has protocol-level ping (opcode 0x9) and pong (0xA) frames (RFC 6455); some protocols use app-level heartbeats instead (e.g. a server sending a tiny message every second when idle).
- **Liveness vs readiness**: liveness = "is the pipe delivering bytes?"; correctness = "did I get *all* the data?" You need both.
- **TCP keepalive**: kernel-level probes. Linux defaults (estimate, check `sysctl net.ipv4.tcp_keepalive_*`): 7200 s idle before first probe, 75 s interval, 9 probes — i.e. >2 hours to notice. Useless as your only defence.
- **Idle timeout**: load balancers and NATs drop idle flows (AWS ALB default is 60 s; many NATs/mobile carriers are shorter or unpredictable — treat as estimates). Your heartbeat interval must be comfortably below the *smallest* idle timeout on the path.
- **Sequence number / offset / cursor**: a monotonic position in the stream so the client can say "I have everything up to N".
- **Gap backfill (catch-up)**: after reconnect, fetch what was missed from a durable source, then splice into the live stream without duplicates or holes.
- **Reconnect storm / thundering herd**: many clients (or one badly-written client) reconnecting in lockstep.

**The four questions every long-lived connection design must answer**
```
1. How do I KNOW it's dead?        -> heartbeat + deadline (watchdog)
2. How do I come back politely?    -> backoff + jitter + circuit/gate
3. What did I miss?                -> sequence/cursor + backfill source
4. How do I avoid double-handling? -> idempotent apply keyed by sequence/id
```

```
client                                server
  |---- connect (auth, last_seq=N) -->|
  |<--- snapshot/replay N+1..M -------|   (backfill)
  |<--- live M+1, M+2 ... ------------|
  |<--- ping --------------------------|   every T
  |---- pong -------------------------> |   deadline = 2-3 x T
  X  (silence > deadline)              |
  |-- close, backoff+jitter, reconnect |
```

## 2. Deep dive (25 min)

### 2.1 Detecting death: who pings whom, and what the deadline is

Rules of thumb:
- **Send pings from the side that cares.** Clients usually need to know the server is alive (to reconnect); servers need to know clients are alive (to free resources). Do both, independently.
- **Deadline = 2–3 missed intervals**, not 1. One missed ping is jitter; three is a dead path. With T = 15 s you detect in ≤45 s (estimate, typical range 10–30 s for T).
- **The watchdog must measure *any inbound traffic*, not just pongs.** Data frames prove liveness too; resetting the timer on every received frame avoids false positives under load and avoids pinging a busy stream.
- **The watchdog must be independent of the read loop's happy path.** If your only timeout lives inside code that is blocked on `await ws.recv()`, a half-open socket blocks it forever. Use `asyncio.wait_for` on the receive, or a separate task that compares `now - last_rx`.
- **Your event loop can be the thing that's dead.** A blocking call or a slow consumer delays the pong, and the *server* drops you. Heartbeat latency measures your own loop health too — pings that are answered late are a signal worth a metric.

```python
import asyncio, random, time

class Feed:
    PING_EVERY = 15      # s; estimate — must be < smallest idle timeout on path
    DEAD_AFTER = 45      # s without ANY inbound frame

    async def run(self):
        attempt = 0
        while True:
            try:
                await self.gate.wait_turn()               # anti-storm gate (see 2.3)
                async with connect(self.url, ping_interval=None) as ws:
                    await self.resume(ws)                 # send last_seq, backfill
                    attempt = 0 if await self.stable(ws) else attempt
                    await self.pump(ws)
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as e:
                log.warning("feed down", extra={"err": repr(e), "attempt": attempt})
            attempt += 1
            await asyncio.sleep(min(60, 2 ** attempt) * random.uniform(0.5, 1.0))

    async def pump(self, ws):
        while True:
            # the deadline lives HERE, around the blocking read
            msg = await asyncio.wait_for(ws.recv(), timeout=self.DEAD_AFTER)
            self.last_rx = time.monotonic()
            self.apply(msg)          # idempotent, keyed by seq
```
Note `reset attempt only after the connection proved stable` (e.g. survived 30 s or received N frames). Resetting backoff on the TCP/TLS handshake succeeding is a classic bug: a server that accepts then immediately closes produces a tight reconnect loop.

### 2.2 Failure modes worth naming in an interview
| Failure | Symptom | Defence |
|---|---|---|
| Half-open socket | Silence, no error | App heartbeat + read deadline |
| Idle-timeout drop by LB/NAT | Drops every N seconds when quiet | Ping interval < path idle timeout |
| Ping loop that never pings | Works until the first idle period, then dies | Test the idle path; assert a ping is *actually emitted* |
| Slow consumer | Server buffer grows, then drops you | Bounded queue; drop/coalesce or disconnect deliberately (Day 9 backpressure) |
| Reconnect storm | Server/IP-level 429/ban, correlated spikes | Backoff + full jitter + global gate |
| Silent gap | Reconnect "succeeds" but data is missing | Sequence numbers, backfill, gap detection |
| Duplicate on resume | Replay overlaps live | Idempotent apply by seq; dedupe window |
| Auth expiry mid-connection | Sudden close with auth error | Refresh token before expiry; treat auth close codes differently from network errors |
| Deploy of the server | All clients reconnect at once | Server-side connection draining, client jitter, staggered rollout |

**Treat close reasons differently.** Network error → retry with backoff. Auth failure → refresh credentials, *don't* hammer. Rate-limit (429/policy close) → long penalty backoff. Protocol/permanent error → stop and alert. One undifferentiated `except Exception: reconnect()` is how storms start.

### 2.3 Reconnect: backoff, jitter, and a global gate
Backoff + jitter was Day 8; the long-lived-connection twist is that **reconnect is a stateful, expensive operation** (auth handshake, subscription replay, backfill query), so it needs a *gate*, not just a delay:
- **Full jitter** (`sleep = random(0, min(cap, base*2^n))`) decorrelates a fleet. For a single client, jitter matters less, but a floor and a cap still do.
- **Gate (token bucket on reconnects)**: no more than K reconnects per window per process/IP, regardless of how many code paths want to reconnect (watchdog, error handler, supervisor, manual retry). Multiple callers each "helpfully" retrying is exactly how counts multiply.
- **Penalty ladder on 429/ban signals**: escalate to minutes, not seconds.

**Your incident, in 30 seconds**: 1,426 reconnects got your client IP blocked. The root cause pattern (state it as such): several independent recovery paths, each with its own retry, no shared budget, no differentiation of "server told me to go away" from "network blip". Fix: one gate all paths pass through, a 429 penalty ladder, and a test that simulates the server refusing. Lesson to say aloud: *the recovery code is the least-exercised code, and it is the code that runs when everything is already on fire.* (That's your essay's thesis.)

### 2.4 Gap backfill: the part most designs skip
Reconnecting restores the *pipe*, not the *data*. Choose by how much loss costs:

| Data | Loss tolerance | Approach |
|---|---|---|
| Typing indicators, cursors | High | Drop; resend latest state |
| Chat, notifications | Zero | Per-stream sequence + durable log; client sends `last_seq`; server replays |
| Market ticks | Depends on use | Live ticks are ephemeral; backfill candles/OHLC from a REST historical endpoint to repair the aggregate, not the tick |
| Financial events / orders | Zero | Sequence + snapshot + replay; reconcile against REST source of truth |

**Resume protocol (the pattern):**
1. Client persists `last_applied_seq` (only *after* applying — at-least-once delivery, idempotent apply; Day 6).
2. On reconnect, it sends `last_seq`. Server replies with either a replay of `last_seq+1..now` or `resync_required` if the gap exceeds retention.
3. **Subscribe to live first, buffer, then fetch backfill, then splice** — dedupe on seq at the seam. Doing backfill first and subscribing second leaves a hole of exactly the backfill duration.
4. If retention is exceeded: full snapshot, then live from the snapshot's seq.
5. Detect gaps *during* normal operation too (`seq != last+1`) — don't wait for a reconnect to discover loss.

When the source has **no sequence numbers** (many broker feeds): you can't prove completeness. Fallbacks: per-instrument timestamps + heartbeat gaps, and *reconciliation against a slower source of truth* (e.g. end-of-minute candle from REST versus candle you built from ticks), alerting on mismatch. State this limitation honestly; it's more credible than pretending ticks are lossless.

### 2.5 Scale: 5 users vs 1M connections
- **5 users, 2 weeks**: use SSE or a managed WebSocket/pub-sub service if you can't justify running your own; plain WebSocket in your FastAPI process; app-level ping every 15–30 s; client reconnect with backoff+jitter; `last_event_id` cursor backed by a Postgres table (a `events(id bigserial, …)` table and `WHERE id > $last` is a perfectly good replay log). No Kafka.
- **~10k connections**: one box can hold them (file descriptors, ~tens of KB per connection — estimate; tune `ulimit -n`). Pain point shifts to fan-out and slow consumers: bounded per-connection queues, drop-or-disconnect policy.
- **1M connections** (estimate: needs dozens to hundreds of nodes; memory-bound at roughly 10–50 KB/conn incl. buffers): a stateless connection tier + pub/sub backbone (Redis streams/NATS/Kafka) so any node can serve any user; connection registry (user → node) or broadcast-by-topic; **server-side draining on deploy** (send "reconnect in random 0–60 s" to spread load); a **reconnect budget** at the edge (rate-limit handshakes, especially TLS+auth, which cost far more than a steady-state connection); backfill served from a log with bounded retention and *a separate, rate-limited replay path* so the herd doesn't also DDoS your database.

The trade-off to say aloud: *the connection tier is cheap in steady state and brutally expensive at the moment of mass reconnect. Capacity-plan for the reconnect, not the steady state.*

### 2.6 Your OSS work as evidence (use sparingly)
- **pykiteconnect #237 forensics**: a ping loop that never actually pings — fine until the path goes idle, then the connection dies and the symptom looks like "the network is flaky". The generalisable lesson: heartbeat code is only tested by *idleness*, which no happy-path test produces. Test by faking a quiet server.
- **DhanHQ-py #65**: a feed client unusable inside a running event loop — connection lifecycle owned by a library that assumes it owns the loop. Lesson: make the connection supervisor a first-class async task you can start, cancel, and observe.
- **pykiteconnect #232 (submitted patch)**: TLS without SNI — handshake-level failure that retries can't fix. Lesson: classify errors; retrying a permanent failure is a storm generator.

## 3. Exercise (15 min)

**Task: write the liveness-and-resume core of a client, with a test that proves it.** Pen/keyboard, 15 minutes, Python.

Write (in a scratch file or in your notes) a `Supervisor` class with:
1. `async run()` — reconnect loop with full-jitter backoff capped at 60 s, resets attempt count only after a connection has been healthy for ≥ 30 s.
2. A read deadline of `DEAD_AFTER` using `asyncio.wait_for` around `recv()`.
3. A `classify(exc) -> {"retry","refresh_auth","penalty","fatal"}` function with at least four exception/close-code cases.
4. Resume: persist `last_seq` after apply; on reconnect send it; on `resync_required` drop local state and snapshot.

**Test (the point of the exercise):** build a fake server coroutine that (a) accepts, sends 3 messages seq 1–3, then goes silent *without closing*; assert your client reconnects within `DEAD_AFTER + ε`; (b) on the second connection receives `last_seq=3` and replays 4–6, with seq 3 duplicated — assert state ends with exactly 1–6 once each.

**Done looks like:** both assertions pass using a fake clock or short deadlines (e.g. `DEAD_AFTER=0.2`), and you can say in one sentence why the half-open case cannot be tested with a server that closes the socket.

If you run short: skip the classify function, write it as a 4-row table of "signal → action" and move on.

## 4. Interview drill (10 min)

**Q1 (0→1). We need live order-status updates in our web app. We have 3 engineers. What do you build?**
<details><summary>Model answer</summary>
Clarify first: how many concurrent users, how bad is a missed update, are updates also visible on refresh? Assuming a few thousand users and "must not miss", I'd build Server-Sent Events (or a single WebSocket) from the existing FastAPI app. Status changes are written to Postgres in the same transaction as an `events` row with a monotonic id; the endpoint streams new rows and accepts `Last-Event-ID` (native in SSE) to resume. Heartbeat comment line every 15–20 s to keep proxies from idling the connection out. Client reconnects with jittered backoff; the UI re-fetches current state on reconnect as a safety net. No separate broker, no Kafka. I'd defer a pub/sub tier until single-node connection count or fan-out latency measurably hurts, and I'd add Postgres LISTEN/NOTIFY or Redis only when I run more than one app node.
</details>

**Q2 (scale). 500k clients are connected and we deploy. What happens and how do you prevent an outage?**
<details><summary>Model answer</summary>
Naively every client drops at once and reconnects within seconds — a herd of 500k TLS handshakes plus auth plus backfill queries. Steady-state capacity is irrelevant; handshake capacity and the backfill datastore are what fall over. Mitigations: roll nodes gradually and *drain* each one — send a "reconnect after random(0, N s)" instruction so clients spread out; client full-jitter backoff; edge rate limit on new connections with 429/Retry-After honoured by clients; serve backfill from a cache or log with its own rate limit, and let clients accept `resync_required` snapshots from a cheap cached path; cap per-client reconnect with a gate. Verify with a load test that kills a node and measures time-to-recover, not just steady-state concurrency. Estimate: handshakes cost orders of magnitude more CPU than an idle connection, so size for them.
</details>

**Q3 (judgment). Would you build exactly-once delivery over WebSockets? What would you NOT build?**
<details><summary>Model answer</summary>
No. Exactly-once across a network is really at-least-once delivery plus idempotent apply, and that is what I'd build: sequence numbers, client-persisted cursor, dedupe by id. I would not build a custom protocol, a bespoke ack/retransmit layer inside the socket, or a multi-region connection mesh before there's evidence. And for ephemeral signals — presence, typing, cursors — I'd explicitly not guarantee delivery; resend latest state periodically instead. Reliability is expensive; spend it only where loss costs money or trust.
</details>

**Q4. Your client shows "connected" but the data stopped 10 minutes ago. How did this happen and how do you stop it recurring?**
<details><summary>Model answer</summary>
Most likely a half-open connection or a stalled event loop, or the subscription was lost server-side while the transport stayed up. Neither is visible to TCP. Fix: a read deadline on the socket independent of the read loop's logic, any inbound frame resets it; application-level heartbeat; and a *data-level* freshness check — "no message for instrument/stream X in N seconds during expected activity" — because transport-alive and data-flowing are different claims. Expose `last_message_age_seconds` as a metric and alert on it (Day 10: symptom-based alerting). Then add a test where the fake server goes silent without closing. From my own work on the NSE feed and a ping-loop bug in pykiteconnect, the heartbeat path only gets exercised when the connection is idle, so it's the path that's most often wrong.
</details>

**Q5. Reconnect succeeded but you suspect missing data. How do you prove completeness?**
<details><summary>Model answer</summary>
If the stream has sequence numbers, completeness is checkable: contiguous seq, gaps trigger a replay request. If it doesn't, I can't prove it from the stream alone, so I reconcile against a slower authoritative source (e.g. REST historical or end-of-period aggregate) and alert on mismatches, plus keep the heartbeat-gap timestamps as evidence of when loss could have occurred. Ordering at the seam: subscribe live and buffer, fetch backfill, splice and dedupe by id.
</details>

**Common mistakes that sink candidates**
1. Saying "I'll just reconnect on error" with no detection of silent failure, no jitter, no cap, and no mention of what was missed in the gap — i.e. solving the pipe and ignoring the data.
2. Resetting backoff on successful connect (not stable operation), or letting several code paths reconnect independently — the exact shape of your own 1,426-reconnect incident, so be ready for the follow-up "how did you know it was that?"

## 5. Go further (optional)
- RFC 6455, The WebSocket Protocol — §5.5.2–5.5.3 (Ping/Pong control frames).
- AWS Architecture Blog, "Exponential Backoff and Jitter" (Marc Brooker) — full jitter vs alternatives.
- Martin Kleppmann, *Designing Data-Intensive Applications*, Ch. 8 (unreliable networks, failure detection) and Ch. 11 (log-based messaging, consumer offsets).
- WHATWG HTML Standard, "Server-sent events" (`Last-Event-ID`, `retry:` field). Links not fetched in this run; search by title.

## Tomorrow
Day 12 — Incident response at a startup: on-call, postmortems, and your incident-command STAR story.
