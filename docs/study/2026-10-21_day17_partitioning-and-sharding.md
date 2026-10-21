# Day 17 — Partitioning and sharding: key choice, hotspots, resharding
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-21*

**Why this matters in a founding-engineer interview** — "How would you scale the database?" is the most common follow-up to any 0→1 design, and the weak answer is "shard it." Interviewers want to hear that you exhaust cheaper options first, that you can pick a shard key by walking through the access patterns, and that you understand resharding is the part that actually hurts. Knowing when *not* to shard is the senior signal.

## 1. Primer (10 min)

**Partitioning** = splitting one logical dataset into disjoint pieces. **Sharding** = partitioning where pieces live on *different machines*. (Postgres "declarative partitioning" splits a table on one node; useful for retention and index size, not for write throughput.)

```
 request(key=user_42) ─▶ ROUTER ─▶ shard_map(key) ─▶ ┌ shard 0 (leader + replicas)
                                                      ├ shard 1
                                                      └ shard 2
```

Vocabulary:
- **Shard key** — the column that decides placement. The single most consequential, hardest-to-change decision.
- **Range partitioning** — contiguous key ranges (A–F, G–M…). Range scans are cheap; monotonic keys (timestamps, auto-increment IDs) pile all writes onto the last range.
- **Hash partitioning** — `hash(key) mod N`. Even spread; range scans become scatter-gather.
- **Directory / lookup-based** — a table maps key → shard. Flexible (move one tenant at a time), but the directory is a critical dependency and must be cached.
- **Consistent hashing / virtual nodes** — keys map onto a ring; adding a node moves ~1/N of keys instead of nearly all (the `mod N` problem). Many small logical shards ("vnodes", e.g. 256–4096) assigned to few physical nodes is the common trick.
- **Hotspot / hot key / skew** — one shard or key takes disproportionate load.
- **Scatter-gather** — a query that must hit every shard and merge; latency = slowest shard (tail latency amplification).
- **Cross-shard transaction** — needs 2PC or sagas; slow, failure-prone. Design so you rarely need one.

## 2. Deep dive (25 min)

### 2.1 The ladder you climb before sharding

Order of operations (cheapest first):
1. **Fix the queries and indexes.** (Day 3.) Most "we need to scale" is a missing index.
2. **Bigger box.** Vertical scaling is absurdly capable: a single Postgres on a large cloud instance (e.g. 64+ vCPU, 512 GB+ RAM, NVMe) handles tens of thousands of simple TPS and multi-TB datasets. *(Estimate; depends heavily on workload.)* The cost of an engineer-month usually exceeds a year of the bigger instance.
3. **Cache** hot reads (Day 15). **Read replicas** for read scaling (Day 16).
4. **Move cold/bulky data out**: archive old rows, put blobs in object storage, time-series/logs into a purpose-built store.
5. **Functional partitioning**: split by *domain* — billing DB, events DB, auth DB. Cheap, reversible-ish, no shard key needed.
6. **Table partitioning on one node** for retention (drop a month = `DROP PARTITION`, not a giant `DELETE`).
7. **Only then: horizontal sharding** — when a single leader can't absorb write throughput or the working set can't fit one node.

Honest triggers for step 7 (estimates): sustained writes beyond what one tuned primary handles (order of 10–50k writes/s for small rows), data in the many-TB range where backup/restore and vacuum/migration times become operational risk, or a hard multi-region data-residency need.

### 2.2 Choosing the shard key — a method

Write down the top 5 queries and the top 3 writes, then score candidate keys:

1. **Does most traffic carry the key?** If 95% of requests are "for tenant X", `tenant_id` lets the router send them to exactly one shard.
2. **Cardinality and evenness.** Key space must be much larger than shard count, and load per key reasonably even. `country` fails; `user_id` usually passes.
3. **Does it co-locate data that is read/written together?** Shard orders *by user_id*, not order_id, if "my orders" is the hot query — and put order items on the same shard. This is what makes single-shard transactions possible.
4. **Is it stable?** If the key changes (user moves region), the row must migrate. Prefer immutable keys.
5. **Does it avoid monotonic hotspots?** Timestamp-leading keys write to one shard. Fix: hash prefix, or composite `(hash(user) , ts)`.

Typical answers:
| Product | Shard key | Why | Pain |
|---|---|---|---|
| B2B SaaS | `tenant_id` | Almost all queries are per-tenant; easy isolation | One giant tenant = a hot shard; need to be able to move/split a tenant |
| Consumer social | `user_id` | "My stuff" queries are local | Celebrity fan-out, "who follows X" is a scatter (Day 20) |
| Messaging | `conversation_id` | Messages in a conversation read together, ordered | Giant groups are hot |
| Market data / ticks | `(symbol, day)` | Reads are per-symbol time ranges | Index-heavy symbols (NIFTY, BANKNIFTY) dominate volume |
| Ledger | `account_id`, but transfers span two | Cross-shard money movement | Needs sagas / a single-writer ledger — often a reason *not* to shard it |

### 2.3 Failure modes

- **Hot shard**: even hash of many keys still breaks if one key is huge. Mitigations: split the hot key (`key#0..k` suffix, write to random, read all k — trades read cost for write spread), isolate the whale onto a dedicated shard (directory-based), or cache/absorb in front.
- **Hot key vs hot shard**: sharding does *not* fix a single hot key; one key lives on one shard by definition.
- **Scatter-gather tail latency**: if each shard's p99 is 50 ms and you fan out to 20 shards, the probability at least one is slow is ~1 − 0.99²⁰ ≈ 18% of requests hit someone's p99. Keep fan-out queries off the hot path; precompute or use a search index.
- **Global uniqueness & ordering lost**: auto-increment IDs per shard collide. Use ULID/UUIDv7/Snowflake-style IDs (time-sortable, shard-local generation).
- **Cross-shard joins/transactions**: either denormalize, co-locate by key, or accept sagas with compensating actions. If a core invariant (money) needs cross-shard atomicity, rethink the key.
- **Secondary indexes**: local (each shard indexes its own rows → query by non-key needs scatter) vs global (separate partitioned index → writes need a cross-shard update). Pick local unless a non-key lookup is hot.
- **Operational multiplication**: N shards = N× backups, migrations, failovers, dashboards. Schema migrations must roll across all shards; tooling debt is real.

### 2.4 Resharding — where the pain lives

`hash(key) mod N` → changing N remaps ~(N−1)/N of keys. Avoid by deciding on **many logical shards from day one** (say 1024), mapped to few physical nodes. Growing = moving logical shards between machines, never rehashing keys.

Live resharding recipe (the standard pattern):
1. Provision target; **dual-write or log-ship** changes (CDC / logical replication) to it.
2. **Backfill** historical rows; verify counts + checksums per range.
3. **Shadow reads**: compare old vs new responses without serving the new.
4. **Cut over** per logical shard via the routing table (small blast radius, instant rollback while old still receives writes).
5. Stop writes to old, keep it read-only for a bake period, then delete.

Never do a "big bang" move; always make the router the one thing you flip.

```python
# Router with many logical shards and a movable map (the part worth getting right)
import hashlib

LOGICAL_SHARDS = 1024                      # fixed forever; decided on day one
shard_map = {i: "pg-0" for i in range(LOGICAL_SHARDS)}   # persisted, cached, versioned

def logical_shard(key: str) -> int:
    h = hashlib.blake2b(key.encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") % LOGICAL_SHARDS

def node_for(key: str) -> str:
    return shard_map[logical_shard(key)]

# Moving shard 17 from pg-0 to pg-1 = copy rows where logical_shard(k)==17,
# keep in sync via CDC, then flip shard_map[17] = "pg-1". Keys never rehash.
```

Note: use a stable hash (not Python's built-in `hash()`, which is salted per process).

### 2.5 Worked example: market ticks

Your NSE feed: say ~2,000 instruments, ticks bursty at the open, a handful of index/option symbols dominating (estimate: top 1% of symbols produce a large share of ticks). Shard by `symbol_id` hash: even for the long tail, but NIFTY options chains concentrate load. Better key: `(symbol_id, minute_bucket)` hashed together to spread a hot symbol over time, with reads for "last 5 minutes of NIFTY" fanning to ≤5 buckets — bounded scatter. Or sidestep: a single TimescaleDB/ClickHouse node handles that volume trivially, which is the real answer at your scale. Story hook: partitioning by *time* on one node bought you retention without any distributed system.

### 2.6 Your LinkedIn angle

You've operated systems where the data layer was already sharded and the shard key had been chosen years earlier. Bring one story on: what the key was, what query it made expensive (the scatter), what a hot member/entity did, and how a migration was staged. The founding-engineer framing: "I've seen what living with a shard-key decision for years costs, so at a startup I delay it, choose logical shards early, and keep tenant isolation as the first split."

### 2.7 Startup vs 1M users
- **5 users, 2 weeks**: one Postgres. Put `tenant_id`/`user_id` on every table now (cheap, makes later sharding mechanical), use UUIDv7 IDs, avoid cross-tenant joins in code paths.
- **~100k users**: replicas, cache, partition big append-only tables by time.
- **1M+ users, write-bound**: functional split, then shard by tenant/user with a directory; whales get their own shard.

## 3. Exercise (15 min)

**Task**: Shard-key memo for a B2B product — "AI agent trace store": customers (tenants) send spans from their agents. Queries: (a) fetch one trace by `trace_id`, (b) list a tenant's last 100 traces, (c) per-tenant daily aggregates, (d) support staff search across all tenants by error text. Tenant sizes: 90% small, a few send 1000× the volume.

Write ≤ 1 page covering:
1. Candidate keys (`trace_id`, `tenant_id`, `(tenant_id, hash(trace_id) % k)`) and a scoring table against the 5 criteria in 2.2.
2. Your pick, how whale tenants are handled, and what happens to query (d).
3. The trigger that makes you shard at all (a number and a metric), and what you do until then.
4. A 5-step resharding plan using logical shards.

**Done =** a recommendation you can say aloud in 90 seconds, with one explicit "I would not shard yet because…" line and one named risk you accept.

## 4. Interview drill (10 min)

**Q1 (0→1).** You're building an MVP multi-tenant product. Do you shard? What do you do on day one to keep the option open?
<details><summary>Model answer</summary>
No. One Postgres with a replica and backups covers us far beyond early-stage load. To keep the option cheap: put a non-null `tenant_id` on every row and in every composite index, use time-sortable globally unique IDs (UUIDv7/ULID) so no auto-increment dependence, keep queries tenant-scoped, avoid cross-tenant joins and foreign keys across tenant boundaries, and route all DB access through one layer so a router can be inserted later. Revisit when a measurable trigger fires — e.g. primary CPU/IO sustained above ~60% at peak after index and query work, or restore time exceeding our RTO.
</details>

**Q2 (scale).** A single tenant is generating 40% of your writes and their shard is melting. What do you do, in order?
<details><summary>Model answer</summary>
Confirm it's one tenant not one query: look at top queries per shard. Immediate: rate-limit or queue that tenant's ingest (backpressure, Day 9) and cache hot reads. Short-term: move that tenant to a dedicated shard via the directory (logical-shard move with CDC, then flip). If a single tenant exceeds one machine, sub-shard their data by a secondary key, e.g. `(tenant_id, hash(entity_id) % k)`, accepting scatter on their aggregate queries — mitigate with pre-aggregated rollups. Longer term, put per-tenant quotas in the contract.
</details>

**Q3 (scale).** You used `hash(user_id) mod 8` and now need 16 shards. How do you do it without downtime?
<details><summary>Model answer</summary>
Naively ~half the keys move. Better: introduce a logical-shard layer (e.g. 1024) now, mapping them to the 8 nodes in a way consistent with the current mod-8 placement (logical shard i → node i mod 8), then split by moving logical shards to new nodes. Per shard: logical replication/CDC to the target, backfill, verify checksums, shadow-read, flip the map entry, keep old read-only for rollback, then drop. Throughout, the router is the single switch and I move a few logical shards at a time to limit blast radius.
</details>

**Q4 (judgment).** A teammate wants to shard the ledger by `account_id`. Thoughts?
<details><summary>Model answer</summary>
Pause. Transfers touch two accounts, so many are cross-shard, forcing 2PC or sagas on the path where correctness matters most. At our volume a single primary with serializable or carefully locked transactions is simpler and safer. If we truly outgrow it, I'd keep a single-writer ledger service (append-only double-entry) and scale reads and everything around it, or shard by a coarser boundary like tenant/legal-entity where transfers stay inside one shard. I'd not trade integrity for horizontal scale until we have a measured need.
</details>

**Q5 (scale/judgment).** Why not just use a database that shards automatically (Cassandra, DynamoDB, CockroachDB, Vitess)?
<details><summary>Model answer</summary>
Sometimes right: DynamoDB if access patterns are key-value and we accept its modeling constraints and cost model; managed distributed SQL if we need relational plus scale. Costs: query flexibility (no ad-hoc joins in Dynamo/Cassandra), operational and cognitive load, vendor lock-in, and the shard key problem doesn't vanish — hot partitions still bite. For a 3-person team I default to Postgres until a specific limit is hit, and I'd write down the trigger that'd make me reconsider.
</details>

**Common mistakes that sink candidates**
- Saying "shard by user_id" instantly without asking about access patterns, or ignoring that one hot key is not fixed by sharding.
- Proposing `mod N` hashing with no story for resharding, or sharding at 10k users.

## 5. Go further (optional)
- *Designing Data-Intensive Applications* (Kleppmann), Chapter 6 "Partitioning" — skew, secondary indexes, rebalancing, request routing.
- Amazon DynamoDB paper (DeCandia et al., "Dynamo: Amazon's Highly Available Key-value Store", SOSP 2007) — consistent hashing and virtual nodes in practice.
- Vitess documentation (vitess.io) — a production resharding workflow worth skimming (VReplication, MoveTables); exact commands are version-dependent.

## Tomorrow
Day 18 — Event streams: the Kafka model, ordering, consumer groups, the exactly-once myth.
