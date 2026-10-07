# Day 03 — Postgres for startups: indexes, transactions and isolation levels, when Postgres is enough
*Week 1: The game, and 0→1 design foundations · ~60 minutes · 2026-10-07*

**Why this matters in a founding-engineer interview** — Yesterday's answer was "v1 monolith + Postgres." Today's follow-up is where founders probe whether you can defend that choice: which index, what happens on concurrent writes, and when you would actually leave Postgres. Candidates who say "Postgres is fine" without being able to explain MVCC, isolation anomalies or a query plan sound like they are reciting; candidates who can say "here is the anomaly, here is the one-line fix" sound like the person who will own the database at 2 a.m.

---

## 1. Primer (10 min)

### Mental model: Postgres is a very good default *because* of four properties

1. **Durable, transactional storage** (WAL, fsync on commit).
2. **MVCC**: readers don't block writers and vice versa. An `UPDATE` writes a *new row version*; old versions linger until `VACUUM` cleans them.
3. **A cost-based planner** that picks between sequential scan, index scan, bitmap scan, and join strategies from table statistics.
4. **Extensibility**: JSONB, full-text search, `pg_trgm`, PostGIS, `pgvector`, `LISTEN/NOTIFY`, `SKIP LOCKED` queues, partitioning. One system covers what would otherwise be 4–5 services.

### Vocabulary

| Term | Meaning |
|---|---|
| B-tree | Default index; equality, range, ordering. ~95% of indexes you write. |
| Composite index | Index on `(a, b, c)`; usable for prefixes `a`, `a,b`, `a,b,c` — **column order matters**. |
| Partial index | `... WHERE deleted_at IS NULL` — smaller, targeted. |
| Covering index | `INCLUDE (col)` lets an index-only scan skip the heap. |
| GIN | Inverted index for JSONB, arrays, full-text. |
| Seq scan | Read the whole table. Not a sin on small tables or when you need most rows. |
| `EXPLAIN (ANALYZE, BUFFERS)` | Run the query and show the actual plan, timings, and buffer hits. |
| Dead tuples / bloat | Old row versions awaiting vacuum. |
| Isolation level | What anomalies concurrent transactions may observe. |

### Isolation levels at a glance (Postgres semantics)

```
Level              Dirty read  Non-repeatable  Phantom        Write skew
READ COMMITTED     no          possible        possible       possible   <- default
REPEATABLE READ    no          no              no (PG snapshot) possible
SERIALIZABLE       no          no              no             no (aborts with 40001)
```

Postgres specifics worth knowing: it never allows dirty reads (READ UNCOMMITTED behaves as READ COMMITTED); its REPEATABLE READ is snapshot isolation, which is stronger than the SQL-standard minimum (no phantoms) but still allows write skew; SERIALIZABLE uses SSI (serializable snapshot isolation) and may abort a transaction, so **your code must retry on SQLSTATE `40001`**.

---

## 2. Deep dive (25 min)

### 2.1 Indexes: the 80/20

**Rule of thumb:** index for the queries you actually run, verified with `EXPLAIN`, not for the queries you imagine.

**What to index in v1**
- Every foreign key you join or filter on (Postgres does *not* auto-index FK columns on the referencing side).
- The columns in your main list queries: `WHERE tenant_id = ? AND created_at < ? ORDER BY created_at DESC LIMIT 50` → `(tenant_id, created_at DESC)`.
- Unique constraints that are business rules (`UNIQUE (tenant_id, external_id)`) — constraints are your cheapest correctness layer, and they double as indexes.

**Composite order:** equality columns first, then the range/sort column. `(tenant_id, created_at)` serves "tenant X, newest first"; `(created_at, tenant_id)` doesn't.

**Cost of indexes:** every index slows writes and takes space; an update touching an indexed column can't use the cheap "HOT update" path. Ten indexes on a hot write table is how a 2-ms insert becomes 15 ms (estimate; measure yours).

**Pagination:** `OFFSET 100000` reads and discards 100k rows. Use keyset pagination (`WHERE (created_at, id) < (?, ?) ORDER BY created_at DESC, id DESC LIMIT 50`). This links to Day 5.

**Worked example — reading a plan**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM ticks
WHERE symbol = 'RELIANCE' AND ts >= now() - interval '1 hour'
ORDER BY ts DESC LIMIT 100;
```
Bad plan: `Seq Scan on ticks ... Rows Removed by Filter: 41,000,000`, `Sort` on top. Fix: `CREATE INDEX CONCURRENTLY ticks_symbol_ts ON ticks (symbol, ts DESC);` → `Index Scan ... Limit` reading ~100 rows. Things to look for: actual vs estimated rows (a big mismatch means stale stats → `ANALYZE`), `Rows Removed by Filter`, `Buffers: shared read` (disk) vs `hit` (cache), and `Sort Method: external merge` (spilled to disk).

Always `CREATE INDEX CONCURRENTLY` on a live table (it avoids blocking writes; it can fail and leave an `INVALID` index you must drop).

*NSE-feed tie-in:* tick data is append-only and time-ordered. At small scale a plain table with `(symbol, ts)` is enough; at hundreds of millions of rows you reach for time-based partitioning (drop a day by `DROP`-ing a partition rather than `DELETE` + vacuum) or TimescaleDB — a trigger-based decision, not a day-one one. Your SQLite experience also matters: SQLite single-writer is fine for one process; the moment two services write, you move to Postgres.

### 2.2 Transactions and the anomalies that actually bite

**Lost update (READ COMMITTED default):**

```python
# BAD: read-modify-write across statements
bal = db.fetchval("SELECT balance FROM accounts WHERE id=$1", acct)
db.execute("UPDATE accounts SET balance=$1 WHERE id=$2", bal - amt, acct)
```
Two concurrent requests both read 100, both write 70. Fixes, in order of preference:

1. **Atomic statement:** `UPDATE accounts SET balance = balance - $1 WHERE id=$2 AND balance >= $1` and check rows-affected. No explicit locking needed.
2. **Pessimistic lock:** `SELECT ... FOR UPDATE` inside the transaction.
3. **Optimistic concurrency:** a `version` column; `UPDATE ... WHERE id=$1 AND version=$2`, retry on 0 rows.
4. **SERIALIZABLE** with a retry loop.

**Write skew (the interview favorite):** "At least one doctor must be on call." Two doctors each check that the other is on call, then both go off call. Each transaction writes a different row, so row-level locking and even REPEATABLE READ don't prevent it. Fixes: lock the *set* (`SELECT ... FOR UPDATE` on all candidate rows), add a constraint that expresses the invariant, materialize the conflict into one row to lock, or use SERIALIZABLE.

**Queue-in-Postgres pattern** (pairs with Day 6):

```sql
SELECT id, payload FROM jobs
WHERE status = 'pending'
ORDER BY id
FOR UPDATE SKIP LOCKED
LIMIT 10;
```
Multiple workers claim different rows without blocking each other. This handles low thousands of jobs/sec on modest hardware (estimate) — more than a seed-stage product needs.

**Idempotency via constraint:** `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id`. If it returns nothing, you've seen this request. This is the cheapest idempotency implementation and it composes with Day 5's API design.

**Operational transaction hygiene**
- Keep transactions short; never hold one open across an HTTP call or LLM call. Long transactions block vacuum (bloat) and hold locks. Set `idle_in_transaction_session_timeout` and `statement_timeout`.
- Lock ordering: always update rows in a consistent order to avoid deadlocks; Postgres detects them and aborts one, so catch and retry.
- Migrations: `ALTER TABLE ... ADD COLUMN` with a constant default is fast on PG 11+; adding `NOT NULL` or a `CHECK` on a big table, or changing a type, can rewrite or lock — use `NOT VALID` then `VALIDATE CONSTRAINT`, and set `lock_timeout` so a migration doesn't queue behind a long query and block everything behind it.

### 2.3 Connections and the first real production problem

Each Postgres connection is a backend process with real memory cost (several MB, more under load). The classic early failure: FastAPI with N workers × a pool of 20 × several instances → hundreds of connections → `too many connections`. Rules:

- Pool in the app (asyncpg / SQLAlchemy pool), sized small (≈ 2–4× cores total across the fleet is a reasonable start; estimate).
- Add **PgBouncer** (transaction pooling) when instance count or serverless/edge functions multiply connections. Caveat: transaction pooling breaks session-level features (session `SET`, advisory locks held across transactions, some prepared-statement setups — driver/version dependent, check yours).
- Managed Postgres (RDS, Cloud SQL, Neon, Supabase) is the right v1 for a founding engineer: backups, PITR, failover are not where your first month should go. Test a restore once; an untested backup is a hope.

### 2.4 Is Postgres enough? — numbers and triggers

Rough, order-of-magnitude estimates on one well-provisioned managed instance with sensible indexes:

| Dimension | Comfortable | Start worrying |
|---|---|---|
| Rows in a table | up to 10⁸ with right indexes/partitioning | 10⁹ without partitioning |
| Write throughput (simple inserts) | thousands/s, 10k+/s batched | sustained 50k+/s on one primary |
| Read throughput | thousands QPS on indexed lookups | read replicas when primary CPU is dominated by reads |
| Data size | hundreds of GB | multiple TB: backups/vacuum/restore time becomes the pain |

**Escalation ladder (each rung only on evidence):**
1. Fix queries/indexes; `pg_stat_statements` shows the top offenders by total time.
2. Vertical scale (cheap, boring, often 10× headroom).
3. Cache hot reads (Day 15), add a **read replica** (and accept replication lag — Day 16).
4. Partition big append-only tables.
5. Move a specific workload out: analytics → columnar/OLAP (ClickHouse, BigQuery), search → dedicated search, queues → Kafka/SQS, vectors → dedicated store *if* `pgvector` stops fitting.
6. Shard (Day 17) — last, and you'll have years of warning.

**"What would NOT go in Postgres"**: large blobs (use object storage + a pointer), high-frequency ephemeral state (rate-limit counters, sessions → Redis, though low volumes are fine in PG), large-scale analytics scans on the OLTP primary, and truly massive event firehoses.

**At LinkedIn scale vs. now:** at LinkedIn you likely used purpose-built, sharded stores with dedicated infra teams. The founding-engineer story is that you know *why* those exist (the escalation ladder) and have the discipline not to adopt them early. Say that directly.

---

## 3. Exercise (15 min)

**Task: schema + concurrency review for a tiny "wallet / credits" service** (e.g., prepaid credits for an AI agent product).

Requirements: tenants have a credit balance; API calls consume credits; top-ups come from a payment webhook that may be delivered twice; the dashboard lists the last 50 transactions per tenant.

Timed:
- **5 min** — write the DDL: `tenants`, `credit_ledger` (append-only entries: `id`, `tenant_id`, `delta`, `reason`, `idempotency_key`, `created_at`), and a balance approach (derived `SUM` vs a cached `balance` column on `tenants` updated in the same transaction). Decide and write one sentence justifying it.
- **4 min** — write the exact SQL for "consume 5 credits if balance sufficient, else fail," safe under concurrency.
- **3 min** — write the index(es) for the dashboard query and the idempotent top-up insert.
- **3 min** — list the three anomalies/failure modes you've guarded against and the one you consciously haven't (and its trigger).

**Done looks like:** a `UNIQUE (tenant_id, idempotency_key)` constraint; a single-statement or `FOR UPDATE` consume path that cannot go negative; an index `(tenant_id, created_at DESC, id DESC)`; ledger entry and balance update in one transaction; and a written trigger, e.g. "if ledger exceeds ~50M rows or balance-lock contention shows in `pg_stat_activity`, then partition/shard by tenant."

---

## 4. Interview drill (10 min)

**Q1 (0→1). Why Postgres for this product and not Mongo/DynamoDB?**
<details><summary>Model answer</summary>

"My data is relational — tenants, users, ledger entries, jobs — and correctness invariants like 'balance never negative' and 'webhook processed once' are enforced best by constraints and transactions I get for free. Postgres also covers JSON documents, full-text search, a SKIP LOCKED queue and even vector search at our scale, so I run one system instead of four. I'd use a managed instance with PITR, test the restore, and write down the triggers that would make me move a workload out: sustained primary CPU from analytics, table sizes where vacuum/backup time hurts, or a dedicated search or queue need. DynamoDB would make sense if I had a known key-value access pattern at very high scale and no ad-hoc querying needs, which is not a seed-stage reality."
</details>

**Q2. Two requests hit your "spend credits" endpoint simultaneously. Walk me through what could go wrong.**
<details><summary>Model answer</summary>

"With read-then-write at READ COMMITTED, both read the same balance and both write, which is a lost update — double spend. I avoid it by making the check and the decrement one statement: `UPDATE ... SET balance = balance - $n WHERE id=$1 AND balance >= $n`, and treating 0 affected rows as insufficient funds. I'd also append a ledger row in the same transaction. If the logic spans multiple rows — e.g., enforcing a cross-account limit — that's write skew, where I'd lock the relevant set with FOR UPDATE, encode the invariant in a constraint, or use SERIALIZABLE and retry on 40001."
</details>

**Q3 (scale). The dashboard query got slow as the table hit 200M rows. What do you do?**
<details><summary>Model answer</summary>

"First, `EXPLAIN (ANALYZE, BUFFERS)` and `pg_stat_statements` — I want evidence, not a guess. Typically it's a missing or wrong-order composite index, an `OFFSET` pagination that should be keyset, or stale statistics. I'd add `(tenant_id, created_at DESC, id DESC)` with `CREATE INDEX CONCURRENTLY` and move to keyset pagination. If the table is append-only and old data is rarely read, I'd partition by time so old partitions can be detached or archived cheaply. Only if reads still dominate primary CPU would I add a replica, accepting lag, and route the dashboard — not the spend path — to it."
</details>

**Q4 (judgment). What would you NOT build in v1 on the data layer?**
<details><summary>Model answer</summary>

"No sharding, no separate queue or search cluster, no CQRS/event sourcing, no multi-region. I'd skip a cache until a measured hot path justifies it, because invalidation is a bug source. I'd keep a plain `jobs` table with SKIP LOCKED instead of Kafka. What I would invest in early is the boring stuff that's expensive to retrofit: constraints, foreign keys, a migration tool, backups with a tested restore, `statement_timeout`, and tenant_id on every table so a later partitioning or row-level-security move is possible."
</details>

**Q5. When would you choose SERIALIZABLE?**
<details><summary>Model answer</summary>

"When the invariant spans multiple rows and I can't cheaply express it as a constraint or a single lock — scheduling, inventory across warehouses, quota across a group. It gives correctness without me reasoning about every interleaving, at the cost of aborts, so every transaction needs a bounded retry loop on 40001 and I keep transactions short. For hot single-row counters, an atomic UPDATE is simpler and faster."
</details>

**Common mistakes that sink candidates**
- Saying "Postgres doesn't scale" or "Postgres scales infinitely" — both are unsupported. Give numbers and a trigger-based ladder.
- Describing isolation levels from the SQL-standard table without knowing Postgres's actual behavior (snapshot-based REPEATABLE READ, no dirty reads) or forgetting that SERIALIZABLE requires application retries.
- Adding indexes "just in case," or proposing `OFFSET` pagination on large tables.

---

## 5. Go further (optional)

- PostgreSQL documentation, "Concurrency Control" chapter (MVCC and Transaction Isolation) and "Performance Tips" (using `EXPLAIN`) — postgresql.org/docs
- *Designing Data-Intensive Applications*, Ch. 7 "Transactions" (write skew, snapshot isolation, SSI)
- Use The Index, Luke — use-the-index-luke.com (composite index order, keyset pagination)

(Links not fetched in this run; verify before relying on them.)

## Tomorrow
Day 04 — Practical drill: a rate limiter in 90 minutes (token bucket vs sliding window) + DECISIONS.md
