# Day 16 — Replication and consistency: leader/follower, read-your-writes, CAP/PACELC in practice
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-20*

**Why this matters in a founding-engineer interview** — The moment you add a read replica (usually to fix a slow dashboard) you have silently changed your consistency model, and the resulting bugs ("I saved it and it's gone") are some of the most common early-startup incidents. Interviewers want to see that you know *which* guarantees your product needs, can name the cheapest mechanism that provides them, and don't recite CAP as if it were a design method.

## 1. Primer (10 min)

**Replication** = keeping copies of the same data on multiple nodes, for (a) durability/failover, (b) read scaling, (c) locality. It does *not* help write scaling (that is Day 17, sharding).

```
            writes                  replication stream (WAL)
 client ──────────────▶ LEADER ───────────┬──────────▶ FOLLOWER A  ◀── reads
                                          ├──────────▶ FOLLOWER B  ◀── reads
                                          └──────────▶ FOLLOWER C (other region, failover candidate)
```

Vocabulary:
- **Leader/follower (primary/replica)** — one node accepts writes; followers apply its log. Postgres streaming replication, MySQL, Redis, Mongo replica sets all work this way.
- **Synchronous vs asynchronous replication** — sync: leader waits for follower ack before commit returns (no lost commits on failover, but a slow/dead follower stalls writes). Async: leader returns immediately (fast, but a failover can lose the last few commits). Common middle: one sync standby, rest async (Postgres `synchronous_commit` + `synchronous_standby_names`; quorum variants exist).
- **Replication lag** — how far behind a follower is. Usually milliseconds; seconds to minutes during a big migration, a long-running query on the replica that conflicts with replay, or network trouble.
- **Failover** — promoting a follower when the leader dies. Hard parts: *detecting* death correctly, *not promoting two* (split-brain), and reconciling writes the old leader had but the new one doesn't.
- **Multi-leader / leaderless (Dynamo-style quorums)** — accept writes at several nodes; need conflict resolution (LWW, CRDTs, app merge). You will almost never choose these at a startup.
- **Consistency vocabulary** (weakest → strongest, roughly):
  - *Eventual* — replicas converge if writes stop; says nothing about when.
  - *Read-your-writes* — you always see your own writes.
  - *Monotonic reads* — you never see time go backwards (a read after a read doesn't return older data).
  - *Consistent prefix* — you see writes in causal order (the answer never appears before the question).
  - *Linearizable* — behaves as if there is one copy and operations take effect atomically at one instant between invoke and return.
- **CAP** — under a network *partition*, you choose between Consistency (linearizability) and Availability (every non-failed node answers). It says nothing about the normal case, and "pick two of three" is misleading: partitions aren't optional.
- **PACELC** — if Partition: A vs C; **E**lse (normal operation): **L**atency vs **C**onsistency. This is the useful half: even with no partition, strong consistency costs round trips.

## 2. Deep dive (25 min)

### 2.1 What actually goes wrong with one leader + async replicas

The three anomalies you will meet, with the fix that costs least:

1. **Read-your-writes violation.** User updates their profile (write → leader), page reloads (read → follower, 200 ms behind), sees old value, files a bug / double-submits.
   Fixes, cheapest first:
   - *Read from the leader for N seconds after that user writes* (store `last_write_at` in the session/cookie; N ≈ 2× p99 lag). 20 lines of code, covers 95% of cases.
   - *LSN/token-based:* after a write, capture the leader's WAL position (`pg_current_wal_lsn()`), pass it with the session; route reads to a replica only if `pg_last_wal_replay_lsn() >= token`, else wait briefly or hit the leader. Precise, more plumbing.
   - *Route by data, not by user:* the "my settings" page always reads the leader; the public feed reads replicas.
   - Pitfall: multi-device. The same user on phone and laptop — the timestamp-in-cookie trick doesn't cross devices; you need server-side state or a token.
2. **Monotonic-read violation.** Two consecutive reads hit two followers with different lag; the second shows older data (a comment appears, then vanishes on refresh). Fix: pin a user/session to one replica (hash of user id), accept that it's per-replica staleness not per-request.
3. **Consistent-prefix violation.** Mostly a sharded/multi-leader problem: replies replicated before the original post. At a one-leader startup it doesn't happen; if an interviewer asks, say so and say why (single log = single order).

```python
# Minimal read-your-writes router, FastAPI-flavoured sketch
STICKY_LEADER_S = 3.0   # estimate: ~2-3x observed p99 lag; MEASURE yours

def pick_db(request, *, writes_something: bool):
    if writes_something:
        return leader
    last = request.session.get("last_write_ts", 0)
    if time.time() - last < STICKY_LEADER_S:
        return leader          # read-your-writes window
    if replica_lag_seconds() > 5:   # lag circuit breaker: degrade to leader
        return leader
    return replica_for(request.user_id)   # stable hash => monotonic reads
```
The lag circuit breaker matters: if lag spikes and you keep sending reads to replicas, you serve very stale data; if you dump everything on the leader you may overload it. Decide which failure you prefer *per endpoint*, and shed load (Day 9) if needed.

### 2.2 Failover: the part everybody hand-waves

Async replication + leader crash = the new leader may be missing the last N ms of commits. Those writes are acknowledged to clients but gone (or sitting on the old leader's disk, to be reconciled or discarded when it returns). Worse: a *discarded* auto-increment id the old leader had already handed out to Redis or another system → id reuse → data corruption across systems (a well-known real-world post-mortem pattern). Related: **split-brain** — a leader that is merely partitioned, not dead, keeps accepting writes while a follower is promoted. Mitigations: fencing tokens / STONITH, a consensus-backed coordinator (Patroni on etcd/Consul for Postgres; managed services do this for you), and a **timeout tuned to be long enough to avoid flapping but short enough to bound downtime** (too short: a GC pause or load spike triggers a needless failover that itself causes an outage).

Startup-sized answer: *use a managed Postgres (RDS/Cloud SQL/Supabase/Neon) with a Multi-AZ standby*; typical automated failover is on the order of 30–120 s (estimate; check provider docs) and the sync standby gives you no committed-write loss on that path. Know your RPO (data you can lose) and RTO (time you can be down) as numbers, and say them out loud. Untested failover is a hypothesis — run one game day before you have customers (ties to your "the code that handles failure is the code nobody tests" essay).

### 2.3 CAP, honestly

- CAP's "C" is *linearizability*, not ACID's "C". Mixing these is the classic interview tell.
- CAP only constrains behaviour *during a partition*. Most of the time there isn't one, so the daily trade-off is PACELC's E-branch: a linearizable read needs to reach the leader (or a quorum) — cross-region that is 60–150 ms of RTT (estimate; Mumbai↔us-east ~200 ms), vs a local replica read at ~1 ms.
- Real systems are per-operation, not per-system: Postgres single node is CP-ish (a partitioned follower stops serving fresh data or serves stale if you let it); Cassandra/Dynamo are tunable (`QUORUM` reads+writes with R+W>N gives you strong-ish reads, `ONE` gives you availability); Spanner buys linearizability across regions with TrueTime and pays commit-wait latency.
- Quorum caveat: R+W>N does **not** by itself guarantee linearizability (sloppy quorums, concurrent writes, LWW clock skew all bite). Say "strong-ish" unless you've argued otherwise.
- Replicas ≠ backups. A bad `DELETE` replicates in milliseconds. You still need PITR (WAL archiving) — a frequent founding-engineer miss.

### 2.4 Which consistency does the product need? (the actual interview skill)

| Feature | Need | Cheapest mechanism |
|---|---|---|
| Profile/settings edit, then redirect | read-your-writes | sticky-to-leader window |
| Feed, search, analytics | eventual (seconds fine) | replicas / cache |
| Account balance, ledger, inventory at checkout | linearizable, per-key | read/write the leader inside a transaction; `SELECT … FOR UPDATE` |
| Unique username / idempotency key | linearizable on one key | unique constraint on leader |
| "Did my webhook fire?" admin view | monotonic reads | stable replica pinning |
| Market tick display | latest-wins, staleness bound | no replica needed; staleness is a *timestamp*, not a consistency model |

Scale shift: **5 users / 2 weeks** → one Postgres, no replica, PITR on. You do not have a consistency problem because you have one copy. **~10k–100k users** → add one read replica *only for* reporting/analytics queries (isolates heavy reads from OLTP), keep product reads on the leader until the leader's CPU says otherwise. **1M users** → replicas for read-heavy surfaces with explicit routing rules, lag SLOs and alerts (lag p99 as an SLI, Day 10), regional replicas, and — only then — sharding for write throughput.

### 2.5 Worked example: "I saved my watchlist and it disappeared"

Symptom: ~1% of users report a saved item missing for a moment. Investigation order: (1) correlate with replica lag graph — spikes during nightly batch → lag > 2 s; (2) confirm route: `GET /watchlist` hits replica; (3) fix: sticky window for the writer + lag breaker; (4) add a metric `reads_served_by_leader_due_to_lag`. Cost: ~1 hour. The *wrong* answer is "make everything go to the leader forever" (kills the reason you added replicas) or "switch to a leaderless database."

Connect to your experience:
- **LinkedIn:** at that scale, feed-like surfaces tolerate seconds of staleness and you've likely seen the member-visible fix pattern ("your own action appears immediately, others eventually"). Prepare one real example: what was stale, how long, what the user could see, who decided it was acceptable. Don't invent figures; use ones you actually know.
- **NSE feed:** after a websocket reconnect you have a gap; the backfill-then-switch-to-live handoff is the same problem as a replica catching up — you need a *position* (sequence number/LSN) to stitch snapshot and stream without duplicates or holes. That's the same idea as the LSN token above.
- **Dhan/Kite clients:** staleness there was visible as a timestamp. Markets systems often prefer an explicit "as-of" over a hidden consistency model — a nice, distinctive line.

## 3. Exercise (15 min)

**Task: write the routing policy for a read replica, as a 1-page memo plus the 15-line routing function.**

Scenario: a Postgres leader + 1 async replica for an AI-eval tracing product (users upload runs, view dashboards, edit eval configs, API clients poll for run status). Replica lag p50 = 40 ms, p99 = 1.5 s, occasionally 30 s during backfills.

Timebox: 5 min classify, 7 min code, 3 min memo.

Do:
1. Classify these six endpoints into {leader-only, sticky-window, replica-ok}: `PUT /eval-config`, `GET /eval-config`, `GET /runs/{id}/status` (CI polls until done), `GET /dashboard`, `POST /runs`, `GET /billing/usage` (used for plan limits).
2. Write `pick_db()` (Python, as above) with the sticky window, a lag breaker and a fallback if the replica is unreachable.
3. Memo (≤ 150 words): the staleness you accept per class, the metric and alert you'd add, and what you'd do during a 30 s lag spike.

**Done looks like:** every endpoint has a one-line justification; the CI-polling status endpoint is *not* on a lagging replica without a reason (a CI job that polls a stale "pending" is merely slow; one that triggers a retry because the replica says "not found" right after `POST /runs` is a bug — spot that, it's the read-your-writes trap in disguise); `/billing/usage` for limit enforcement is on the leader or has a conservative bound; the memo names a number for the window and says how you'd tune it.

## 4. Interview drill (10 min)

**Q1 (0→1). You have one Postgres and the dashboard is slow. Do you add a read replica?**
<details><summary>Model answer</summary>
Not first. I'd look at the slow queries (`pg_stat_statements`, `EXPLAIN ANALYZE`), add missing indexes, and maybe a cache or a materialized view. If the load is heavy analytical reads contending with OLTP, a replica dedicated to reporting is a cheap win and low-risk because those queries tolerate staleness. I'd not route product reads there on day one: that's when read-your-writes bugs start. If I do, I add sticky-to-leader after writes and a lag alert before launch.
</details>

**Q2. A user says "I changed my setting and it reverted." Walk me through it.**
<details><summary>Model answer</summary>
Suspect replication lag causing a read-your-writes violation before a data-loss bug. Check replica lag at that timestamp, confirm the read path hit a replica, and check whether it reverted permanently (a real bug: lost update, or failover lost an async commit) or only briefly (staleness). Fix with a sticky leader window or LSN token for that user, add a metric for reads routed to leader due to lag, and write down the staleness budget per endpoint. If it was permanent, look at concurrent writes (last-write-wins between two tabs) and at whether a failover recently happened.
</details>

**Q3 (scale). The product goes global — users in India and the US. Where do the replicas and the leader live?**
<details><summary>Model answer</summary>
Writes still go to one leader in the region with most writers (or the most latency-sensitive writers). Add a regional read replica for the other region and serve the stale-tolerant surfaces locally (~1 ms vs ~200 ms cross-ocean RTT, an estimate). Writers then pay the round trip on writes, which is usually acceptable; read-your-writes is handled by sticky-leader or LSN tokens. I'd resist multi-leader until write latency is a measured business problem: conflict resolution is a permanent tax. If data residency forces it (e.g. India-only financial data), I'd partition by tenant/region rather than replicate conflicting writes.
</details>

**Q4 (judgment). What would you NOT build?**
<details><summary>Model answer</summary>
Multi-leader or a Dynamo-style leaderless store for a product with one write region — conflict handling would dominate the roadmap. I wouldn't self-manage Postgres failover (Patroni, etc.) with a team of five when a managed Multi-AZ gets me there, and I wouldn't claim "strong consistency" for a cache + replica topology. I'd also not build a bespoke LSN-token router before a three-second sticky window has proven insufficient.
</details>

**Q5. Explain CAP in the context of a system you built.**
<details><summary>Model answer</summary>
CAP is about the partition case. In my market-data client, a partition (or half-open TCP) means I either serve the last tick with an "as-of" timestamp (availability, stale) or refuse to show a price (consistency). For trading decisions I chose to flag staleness loudly and block orders past a bound; for a display-only watchlist, show last-known with a stale marker. The more day-to-day trade-off was PACELC's latency side: fresh data costs a hop. The point is that the choice is per-feature and explicit, not a database label.
</details>

**Common mistakes that sink candidates**
- "Pick two of CAP" — no mention of partitions, or conflating CAP's C with ACID's C.
- Adding replicas as a free speedup, with no read-your-writes, lag, or failover answer; or claiming "replicas are our backups."

## 5. Go further (optional)
- Kleppmann, *Designing Data-Intensive Applications*, ch. 5 (Replication) and ch. 9 (Consistency and Consensus); also his essay "Please stop calling databases CP or AP".
- Abadi, "Consistency Tradeoffs in Modern Distributed Database System Design" (IEEE Computer, 2012) — the PACELC paper.
- PostgreSQL documentation: "High Availability, Load Balancing, and Replication" (links not verified in this run).

## Tomorrow
Day 17 — Partitioning and sharding: key choice, hotspots, resharding.
