# Day 21 — Review + Mock: design a news feed / activity stream
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-25*

**Why this matters in a founding-engineer interview** — The feed prompt is the standard test of whether you can pick the simplest design that survives a real workload and name the number at which it breaks. Today is a full timed mock plus a recall pass over Week 3 (caching, replication, sharding, streams, war stories, fan-out), so you rehearse stitching those topics into one coherent answer under pressure.

## 1. Primer (10 min) — Week 3 in one page

Re-read this, then close it and say each line aloud from memory.

| Topic | The one-line mental model | The number/rule to quote |
|---|---|---|
| Caching (D15) | Cache-aside; invalidation is the hard part; protect against stampedes (single-flight, jittered TTL, stale-while-revalidate) | Cache IDs and small objects; a 90% hit rate means the DB still sees 10% of traffic — size the DB for the miss path |
| Replication (D16) | Async followers give read scale, not write scale; lag breaks read-your-writes | Fix with sticky-to-leader after write, or a "last write LSN/timestamp" token — not by going synchronous everywhere |
| Sharding (D17) | Shard key = your most common access path; hotspots come from skew, not hashing | Don't shard until one Postgres box (vertical + replicas + partitioning) is truly exhausted — typically well beyond 1–5 TB / several thousand write TPS (estimate; depends on workload) |
| Streams (D18) | A log with per-partition ordering and consumer-group offsets; "exactly-once" = at-least-once + idempotent sink | Order only holds within a partition; choose the key for the entity whose order matters |
| Feeds (D20) | Push = O(1) read, O(followers) write; pull = reverse; hybrid for high fan-out authors | Switch to hybrid when write amplification per post becomes the dominant cost, not before |
| War stories (D19) | Situation → constraint → your decision → quantified result → what you'd do differently | Every story needs one number and one thing you *rejected* |

How the pieces compose in a feed (this is the diagram you'll draw today):

```
 client ──► API ──► Postgres (posts, follows)  ◄── source of truth
              │          │ outbox / CDC
              │          ▼
              │      stream (partition key = author_id)
              │          │
              │          ▼
              │      fan-out workers (idempotent: ZADD is naturally idempotent)
              │          │
              ▼          ▼
         feed read ◄── Redis ZSET per viewer (post_id by score)  ← timeline cache, bounded (e.g. 800)
              │
              └─► merge: celebrity posts pulled live at read time
              └─► hydrate: batch-get posts/authors/counts (cache-aside, MGET)
```

## 2. Full mock (25 min, timed — no peeking at the model walkthrough until the clock ends)

Set a 25-minute timer. Talk out loud or write bullets in a scratch file. Treat me as the interviewer.

### The prompt (as the interviewer would say it)

> "You're the first engineer at a professional-network startup. We have about 20k users, expect 10x in a year if things go well. Users follow each other and post short updates. Design the home feed — the screen people see when they open the app. You have two weeks to ship v1, and we'll have a second engineer in three months."

### Clarifying questions you should ask (pick ~5, don't ask all)

1. Chronological or ranked? (Assume chronological for v1; ranking is a later product decision.)
2. What is the follow graph shape — symmetric connections (LinkedIn) or asymmetric follows? Is there a max connection count? Any accounts with 100k+ followers?
3. Read:write ratio and peak concurrency? How often does a user open the feed per day?
4. Freshness requirement — must a post appear within seconds, or is a minute fine? Must the author see their own post instantly?
5. What does a feed item contain — text only, media, reactions, comments? Are counts (likes) needed live?
6. Deletes/edits/blocks/privacy — must a deleted post vanish immediately everywhere?
7. Do we need notifications or an activity stream for the same events (separate surface)?

Good answers you can assume if the interviewer shrugs: 20k users, ~5k DAU, ~1 post/user/day among ~20% posters, median 150 followees, max 5k, no celebrities yet, 10–20 feed opens/day/user, freshness within ~10s, chronological.

### Staged model walkthrough

**Stage 0 — Requirements and back-of-envelope (3 min).**
Estimates: 5k DAU × 15 opens = 75k feed reads/day ≈ ~1 read/sec average, maybe 10–20/sec peak. Posts: ~1k/day. Even at 10x that's trivially small. State it explicitly: *"At this scale the interesting constraint is engineering time, not throughput."*

**Stage 1 — v1: fan-out on read, one Postgres (5 min).**
```sql
CREATE TABLE posts   (id BIGSERIAL PRIMARY KEY, author_id BIGINT, body TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE follows (follower_id BIGINT, followee_id BIGINT, PRIMARY KEY (follower_id, followee_id));
CREATE INDEX posts_author_created ON posts (author_id, created_at DESC, id DESC);

-- feed query, keyset pagination on (created_at, id)
SELECT p.* FROM posts p
JOIN follows f ON f.followee_id = p.author_id AND f.follower_id = $viewer
WHERE (p.created_at, p.id) < ($cursor_ts, $cursor_id)
ORDER BY p.created_at DESC, p.id DESC
LIMIT 20;
```
Why this wins in week one: one table of truth, no cache invalidation, no queue, no consistency bugs, deletes and blocks are just a `WHERE`. Own posts appear instantly because there's nothing to propagate. Ship in days.

Say what you're *not* building: no Redis, no Kafka, no ranking, no per-user timelines.

**Stage 2 — Where v1 breaks and how you'd know (4 min).**
- Metric to watch: p95 of the feed query and rows scanned per request. With 150 followees × recent-window index scans, it's comfortable at this scale. It degrades as median followees grows (say 500+) and posts-per-day grows — you merge N sorted index ranges per request.
- First fixes, in order of cost: (1) cap the time window (last 7 days) — cheap; (2) read replica for feed reads (accept lag; own-post read-your-writes via leader-after-write); (3) cache the *hydrated post objects* cache-aside, not the feed; (4) short-TTL cache of the first page per viewer (e.g. 5–10s, jittered).
- Resist: sharding, Kafka, or push timelines at this point.

**Stage 3 — Evolution to push/hybrid (6 min).**
Trigger: feed read p95 > budget (e.g. 300 ms) *after* the cheap fixes, or feed reads dominate DB CPU.
- Build per-viewer timelines in Redis: `ZSET feed:{viewer}` with `post_id` scored by timestamp, **bounded** (e.g. 800 entries via `ZREMRANGEBYRANK`). Postgres stays the source of truth; the ZSET is a rebuildable cache — if lost, rebuild from the pull query.
- Write path: insert post → write to an outbox row in the same transaction → relay to a stream partitioned by `author_id` → idempotent worker does `ZADD` for each follower. Retries are safe since `ZADD` of the same member/score is a no-op.
- Hybrid threshold: authors above a follower cutoff (pick from data; a common starting point is on the order of 10k, estimate) are *not* fanned out. At read time, merge the viewer's ZSET with a pull of recent posts from the few celebrity followees they follow (cached per author, shared across all viewers).
- Cold/inactive users: don't push into timelines of users inactive for 30+ days; rebuild on return via the pull path.
- Hydration: `MGET post:{id}`; miss → batch SELECT; cache-aside with jittered TTL; counts from a separate cache since they change faster than bodies.
- Pagination: cursor = `(score, post_id)`; never OFFSET.
- Deletes/blocks: filter at hydrate time (tombstone check) rather than chasing every timeline entry.

**Stage 4 — Scale to 1M+ users (4 min).**
- ~1M users, 20% DAU ≈ 200k DAU × 15 opens ≈ 3M reads/day ≈ 35/sec avg, ~100–300/sec peak (estimates). Redis handles this on a single node; the cost is memory: 800 IDs × ~16–20 B per ZSET entry × 1M users ≈ 13–16 GB worst case if everyone has a full timeline (estimate; cap or evict inactive users to cut it).
- Write amplification: 200k posts/day × 150 followers = 30M ZADDs/day ≈ 350/sec average — fine; a 100k-follower author is a 100k-ZADD burst per post, which is exactly why hybrid exists. Rate-limit/queue those bursts so they don't starve normal fan-out (separate queue/priority lane, bulkhead — Day 9).
- Sharding Redis by viewer_id when memory or ops/sec demands it; Postgres: partition `posts` by time, shard by `author_id` only once a single primary's write or storage ceiling is hit.
- Ranking: introduce a candidate-generation → scoring → re-rank pipeline; the timeline holds *candidates*, and the ranker runs at read time. This is a different system, so mention it and defer it.
- Observability: feed latency SLO, fan-out lag (post created → in timeline, p99), timeline-miss rate, rebuild rate, hydrate cache hit rate.

**Where your own experience fits (use as seasoning, not the headline).**
- LinkedIn: say what you actually know firsthand about feed or activity-stream data flow (don't claim internals you didn't work on) — e.g. the instinct of "treat derived timelines as rebuildable caches off a log" and living with at-least-once pipelines.
- NSE feed: a market tick stream is the same shape as fan-out — one producer, many consumers, bursts, and the reconnect/backfill problem. Your anti-reconnect-storm gate is the same lesson as the fan-out backpressure lane: bursts from one source must not take down shared capacity.

### Self-scoring rubric (score yourself 0–2 per row, total /20)

| # | Criterion | 0 | 1 | 2 |
|---|---|---|---|---|
| 1 | Clarified before designing (≥3 meaningful questions) | none | generic | graph shape, freshness, ranking |
| 2 | Did back-of-envelope early and used it to decide | none | numbers but unused | numbers drove "no cache/queue yet" |
| 3 | Chose a v1 shippable in two weeks | jumped to Kafka/Redis | reasonable but heavy | single Postgres, keyset pagination |
| 4 | Named the trigger for each evolution (metric + threshold) | "when it's slow" | one trigger | trigger per stage |
| 5 | Explained push/pull/hybrid trade-off correctly | wrong/recited | correct, no numbers | correct with amplification math |
| 6 | Treated timelines as rebuildable cache; source of truth explicit | no | implied | stated, with rebuild path |
| 7 | Handled failure modes (idempotent fan-out, lag, deletes, celebrity burst) | none | 1–2 | ≥3 with mechanisms |
| 8 | Said what you would NOT build and why | no | vague | specific and justified |
| 9 | Communication: structure, signposting, pacing within time | rambling | ok | clear stages, finished on time |
| 10 | Tied in real experience credibly (1–2 sentences, not a monologue) | none/overclaimed | generic | specific, honest |

16+: ready. 12–15: redo the weak rows tomorrow in 10 minutes. <12: redo the full mock on the weekend.

## 3. Quick recall quiz (15 min total with writing out answers — 5 questions)

Answer each in two sentences before opening the answer.

**Q1.** A cache entry for a hot key expires and 2,000 requests hit Postgres at once. Name three mitigations and which you'd ship first at a 5-person startup.
<details><summary>Answer</summary>
Stampede. Mitigations: single-flight/request coalescing per key (one in-flight recompute, others wait), jittered TTLs so keys don't expire together, and stale-while-revalidate (serve stale, refresh in background); also probabilistic early refresh. Ship first: single-flight plus TTL jitter — both are a few lines in-process, no new infrastructure.
</details>

**Q2.** A user posts, refreshes, and their post is missing. Your reads go to an async replica. Give two fixes and the cost of each.
<details><summary>Answer</summary>
Replica lag broke read-your-writes. Fix 1: after a write, route that user's reads to the leader for a short window (e.g. a few seconds, via cookie/session flag) — cheap but adds leader load. Fix 2: return a write token (LSN/timestamp) and have the read wait for a replica to catch up or fall back to the leader — more precise, more plumbing. Alternative: merge the user's own just-written post client-side/server-side into the response.
</details>

**Q3.** You chose `user_id` as the shard key for an activity table, and one customer holds 40% of the traffic. What went wrong and what are your options?
<details><summary>Answer</summary>
Hotspot from skewed key cardinality — hashing doesn't fix one hot key. Options: isolate the big tenant on its own shard (tenant-aware routing/directory), add a secondary key to spread its rows (e.g. `(tenant_id, bucket)`) at the cost of scatter-gather reads, or change the key if the dominant access path is different. Directory-based sharding makes this move cheap; pure hash-mod makes resharding painful.
</details>

**Q4.** Your fan-out worker crashes after writing to 60% of a post's followers' timelines, and the stream redelivers the message. Is this a bug? What property makes it fine?
<details><summary>Answer</summary>
Not a bug if the sink is idempotent: `ZADD feed:{u} score post_id` with the same member and score is a no-op, so reprocessing converges. This is "at-least-once delivery + idempotent consumer = effectively-once effects"; true exactly-once across systems is not available without transactional coordination. Partition by author so a given author's posts are processed in order.
</details>

**Q5.** In one breath: when do you move from pull to push, and when do you add the celebrity exception?
<details><summary>Answer</summary>
Move to push when feed-read latency or DB CPU from the merge-N-followees query misses your SLO even after windowing, replicas and hydration caching — read amplification is now the dominant cost. Add the celebrity (hybrid) exception when write amplification for a few high-follower authors (burst of tens of thousands of timeline writes per post) starts causing fan-out lag for everyone else; pull their posts at read time instead.
</details>

## 4. Interview drill (10 min)

Different from the mock: follow-ups an interviewer will throw after your design.

**1. (0→1) "You said single Postgres with a join. Defend that — isn't it naive?"**
<details><summary>Model answer</summary>
It's deliberate. At ~1 read/sec average, a keyset-paginated indexed query returns in single-digit milliseconds; the alternatives (Redis timelines, queues) add invalidation, backfill and failure modes I'd have to operate with one engineer. I've written down the trigger: feed p95 over 300 ms or feed queries over, say, 30% of DB CPU after windowing and a replica — that's when I build timelines. Deleting a post or blocking a user is a `WHERE` clause in v1 and a distributed cleanup problem in v2, so I want the data to justify that cost.
</details>

**2. (scale) "A user with 3M followers posts. Walk me through what happens."**
<details><summary>Model answer</summary>
Under hybrid, nothing special at write time: the post is stored and the author is flagged high-fan-out, so no timeline writes. At read time, each viewer's feed merges their ZSET with the latest posts from followed celebrities, fetched from a per-author cache shared across all viewers — so the 3M followers cause 3M cheap cache reads over time, not 3M writes in one burst. If I were fully push, that's 3M ZADDs queued behind everyone else's fan-out; the p99 fan-out lag would spike for normal users, so I'd isolate it in a separate low-priority queue as a fallback.
</details>

**3. (judgment) "What would you NOT build for the first six months?"**
<details><summary>Model answer</summary>
ML ranking (we have no engagement data or a proven need; chronological is a legitimate product), a Kafka cluster (a Postgres outbox or a simple job queue covers fan-out), sharding (one primary with replicas goes far past our growth curve), and real-time push of feed updates over websockets (pull-to-refresh and a "new posts" poll satisfy users). I'd instrument fan-out lag and feed latency from day one so the decision to add any of these is made on numbers.
</details>

**4. "A user blocks another user. How fast must the blocked user's posts vanish from their feed?"**
<details><summary>Model answer</summary>
Treat it as a correctness/safety requirement — immediate for the blocker. In v1 it's a join/anti-join, so instant. With timelines, I'd filter at hydrate/read time against a cached block set rather than rewriting timelines; entries age out of the bounded ZSET naturally. Same pattern for deletes: a tombstone check at hydration is cheaper and safer than chasing every copy.
</details>

**5. "Your Redis timeline store loses all data. What happens?"**
<details><summary>Model answer</summary>
Nothing is lost permanently since timelines are derived. Reads fall back to the pull path (slower but correct) and a rebuild job repopulates active users first, rate-limited so it doesn't DDoS Postgres — a retry-storm in disguise, same as reconnect storms. I'd test that rebuild path regularly, because failure-handling code that's never exercised is the code that fails (the essay's thesis).
</details>

**Common mistakes that sink candidates**
- Opening with Kafka + Redis + sharding for a 20k-user product; the interviewer is grading judgment, not vocabulary.
- Reciting "push vs pull" without numbers or a trigger metric; and forgetting hydration, deletes/blocks and pagination, which is where the real work lives.

## 5. Go further (optional)

- *Designing Data-Intensive Applications* (Kleppmann), Ch. 1 — the Twitter timeline example of fan-out and load parameters (the canonical write-up of this exact trade-off).
- *DDIA* Ch. 5 (replication lag, read-your-writes) and Ch. 11 (stream processing, idempotence).
- Redis docs, sorted sets (`ZADD`, `ZREMRANGEBYRANK`) — redis.io/docs — to confirm the commands used above.

## Tomorrow

Day 22 — LLM application architecture: context, retrieval basics, latency and cost budgets (Week 4 starts).
