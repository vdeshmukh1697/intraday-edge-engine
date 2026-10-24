# Day 20 — Feeds: fan-out on write vs fan-out on read
*Week 3: Data at scale (Register A: your LinkedIn war stories) · ~60 minutes · 2026-10-24*

**Why this matters in a founding-engineer interview** — "Design a news feed" is the most-asked at-scale prompt there is, and with LinkedIn on your résumé the interviewer will assume you know it cold. The trap is reciting Twitter's push/pull diagram; the signal is choosing the *dumbest* design that survives your actual graph shape, and naming the exact number at which you'd change it.

## 1. Primer (10 min)

A feed is a **per-viewer, ordered, paginated list of items from sources the viewer follows**. The whole problem is one asymmetry: writes happen once per post, reads happen once per *viewer per open* — and the number of viewers per post (fan-out) varies by orders of magnitude.

Two pure strategies:

```
FAN-OUT ON READ (pull)                    FAN-OUT ON WRITE (push)
post:  INSERT into posts                  post:  INSERT into posts
                                                 for f in followers(author): LPUSH feed:f post_id
read:  followees = follows(viewer)        read:  ids = LRANGE feed:viewer 0 N
       SELECT ... FROM posts                     hydrate(ids)  -- posts, authors, counts
       WHERE author IN followees
       ORDER BY ts DESC LIMIT N
cost:  write O(1), read O(followees)      cost:  write O(followers), read O(1)
```

Vocabulary:
- **Fan-out factor** — followers per author (write amp for push) / followees per viewer (read amp for pull).
- **Hybrid (push/pull)** — push for normal authors, pull-and-merge for "celebrities" (high fan-out) at read time.
- **Hydration** — turning a list of IDs into renderable objects (post body, author, like counts). Usually the real read cost.
- **Timeline cache / feed store** — per-user list of post IDs (Redis list/zset, or a wide-column row). Holds IDs, not bodies.
- **Ranking** — ordering by predicted relevance instead of time. Changes the architecture more than push-vs-pull does (see §2).
- **Cursor pagination** — (score, id) keyset, never OFFSET; feeds mutate while you scroll (Day 5).

Reverse-chronological vs ranked is the first question to ask, because ranked feeds usually need a **candidate generation → ranking → filtering** pipeline, and push-vs-pull becomes a question about *where candidates come from*.

## 2. Deep dive (25 min)

### 2.1 The cost model — do the arithmetic first

Say 1M DAU, each posts 0.5×/day, opens the feed 10×/day, average 200 followees/followers.

- Pull: 10M feed loads/day × (query across ~200 authors). ~115 loads/s average, maybe 5× at peak ≈ 600/s. Each load is a 200-way merge. Doable on Postgres with the right index until it isn't.
- Push: 500k posts/day × 200 followers = 100M feed-list inserts/day ≈ 1,150 writes/s average, ~6k/s peak. Cheap in Redis, but it's **200× write amplification**, and every one of those lists must be stored (100M entries × ~16 B ID + overhead ≈ low tens of GB — an *estimate*, depends on structure).

At that size both work. Which means: the *workload shape*, not the user count, picks the winner. Read:write ratio of feed loads to posts here is 20:1 — favours push. If it were 1:1 (rarely-opened feeds), push is mostly wasted work on feeds nobody reads.

### 2.2 The celebrity problem (and its cousin: inactive users)

One author with 20M followers posting once = 20M list inserts. At ~100k inserts/s from a fleet of workers that is minutes of lag, a thundering herd on the queue, and a write storm on whichever shards hold those feeds. The standard answer: **don't push for authors above a follower threshold** (Twitter's often-cited ballpark is ~hundreds of thousands to millions; treat as order-of-magnitude, tune on your graph). At read time, merge the viewer's pushed list with a pull of recent posts from the followed celebrities:

```python
def read_feed(viewer, cursor, limit=30):
    pushed = feed_store.range(viewer, cursor, limit)             # [(ts, post_id)] precomputed
    celebs = follows.celebrity_followees(viewer)                 # small set, cached
    pulled = posts.recent_by_authors(celebs, before=cursor, limit=limit)  # indexed (author_id, ts DESC)
    merged = heapq.merge(pushed, pulled, key=lambda x: -x[0])    # both already sorted
    ids = list(itertools.islice(merged, limit))
    return hydrate(ids)                                          # batch fetch, not N+1
```

Failure modes of the hybrid:
- **Threshold flapping.** An author crosses the line; their old posts are in follower lists, new ones aren't. Fine — merge-by-timestamp makes the transition invisible. Don't try to "migrate" lists.
- **Dual code paths** double your bugs. The hybrid is a *scale* feature; don't build it at 5 users.
- **Inactive followers.** Pushing into feeds of users who haven't logged in for 90 days is pure waste. Push only to users active in the last N days; rebuild the feed lazily (a pull) on their return. This often cuts write amplification by 2–5× (estimate; depends on your retention curve) and is cheaper than the celebrity fix.

### 2.3 Consistency, deletes, and edits

Feed lists hold IDs, so most mutations are free: edit a post and hydration returns the new body. The hard ones:
- **Deletes/blocks/privacy changes.** Filter at hydration (check post status, block lists) — never try to scrub millions of lists synchronously. Lazy filtering plus background cleanup. A deleted post that still appears for a minute is a bug; one that appears after a *block* is a trust incident — filter blocks at read, always.
- **Read-your-writes (Day 16).** The author must see their own post immediately even if fan-out is async. Insert into the author's own feed synchronously in the request, fan out the rest via queue/outbox (Day 6).
- **Duplicates.** At-least-once fan-out workers (Day 18) will double-insert. Use a sorted set keyed by post_id (idempotent `ZADD`) or dedupe at read.
- **Gaps and ordering.** Fan-out is async, so two posts can land in a feed out of order. Sort by (ts, id) on a server-assigned timestamp/Snowflake-style ID; accept that a feed is eventually complete.

### 2.4 Ranking changes the architecture

With ranking you generally can't precompute one final list per user, because scores depend on request-time features (recency, affinity, the viewer's current session). The usual shape: push/pull produces a **candidate set** (say the latest ~500–1000 items from graph + some recommended sources), a model scores them at read time, a filtering pass removes seen/blocked/duplicate items, and a diversity pass reorders. Consequences: the feed store becomes a candidate store; latency budget (~200–400 ms p99 is a typical target, estimate) is dominated by feature fetch and model scoring; and **"seen" state** becomes a first-class write path. LinkedIn's feed is of this kind, which is why any LinkedIn-flavoured follow-up goes to ranking and quality, not just storage.

### 2.5 What changes with scale

| | 5 users / 2 weeks | ~1M users | 100M+ |
|---|---|---|---|
| Strategy | Pull. One SQL query. | Push for most, pull-merge for outliers, active-user gating | Hybrid + ranked candidate pipeline |
| Storage | `posts` + `follows` in Postgres, index `(author_id, created_at DESC, id)` | Redis zsets / Postgres feed table for IDs; posts in Postgres | Sharded feed store (by viewer), posts sharded by author, separate counts service |
| Hydration | JOINs | Batched multi-get + short-TTL object cache (Day 15) | Dedicated services, request coalescing |
| Hard problem | Shipping | Celebrity writes, hot keys | Ranking latency, freshness, cost |

Partition key choice (Day 17): the feed store shards by **viewer_id** (a read touches one shard); posts shard by **author_id** (a pull touches few shards). Pull across 200 authors on a sharded posts table is a scatter-gather — another reason pull gets expensive at scale.

### 2.6 Worked example: the pull query that carries you to ~100k users

```sql
CREATE INDEX posts_author_ts ON posts (author_id, created_at DESC, id DESC);

SELECT p.id, p.created_at
FROM posts p
JOIN follows f ON f.followee_id = p.author_id AND f.follower_id = $1
WHERE (p.created_at, p.id) < ($2, $3)        -- keyset cursor
ORDER BY p.created_at DESC, p.id DESC
LIMIT 30;
```

With ~200 followees this is fast if each author has the composite index and the planner does a merge/limit per author; it degrades when followees reach thousands or a few authors have huge histories. Check `EXPLAIN (ANALYZE, BUFFERS)` before believing any of this — plan shape is data-dependent. A bounded "recent window" (`created_at > now() - interval '14 days'`) keeps it honest.

### 2.7 Your experience, as story fuel

- **NSE feed (Day 11 theme):** a market-data tick stream is *fan-out on write with a hot key*: one symbol (NIFTY options near expiry) dominates traffic, exactly the celebrity shape. Your gap-backfill logic is the feed's "rebuild lazily on return" in another costume — a client that was disconnected re-pulls from a durable source instead of the server replaying every missed push.
- **LinkedIn:** you have operated systems where "who sees what" is derived data. Prepare one honest, quantified sentence about a time derived data went stale or a hot key hurt you (Day 19 workshop output) and use it when the interviewer asks "what goes wrong?"

## 3. Exercise (15 min)

**Task: write the feed decision memo and the capacity table.** Timed, pen or keyboard.

1. (5 min) Pick a concrete product: "a founder-community app with 50k users in 12 months, avg 150 followees, posts/day = 0.3/user, feed opens = 6/user/day, a few power users at ~20k followers." Compute: feed loads/s (avg and 5× peak), post writes/s, push write amplification/s, pull fan-in per read.
2. (7 min) Write a half-page memo, `FEED_DECISION.md`: chosen strategy, the one number that would make you switch (e.g. "when p95 pull latency > 300 ms or followers-per-author p99 > 5k"), what you're *not* building (ranking, hybrid, recommendations), and your delete/block handling.
3. (3 min) Sketch the read path as pseudocode like §2.2 but for your v1 choice.

**Done looks like:** four computed numbers with units, a named switch trigger with a metric, ≥3 explicit "not building" items, and a v1 query with the index you'd create. Sanity check: at that size pull should win on simplicity; if you chose push, your memo must justify the extra moving parts.

## 4. Interview drill (10 min)

**Q1 (0→1). "You're the first engineer on a social product. Design the feed."**
<details><summary>Model answer</summary>

Clarify: chronological or ranked? Expected followees/followers, read:write ratio, any power users? Then: v1 is `posts` + `follows` in Postgres, composite index `(author_id, created_at DESC, id DESC)`, keyset pagination, one SQL query, batched hydration, cache author/count objects. Ship in days. State the number: "I move to push when feed-load p95 passes ~300 ms or loads/s exceeds what one replica handles — I'd know from the dashboard before users do." Then outline the next step (Redis zset of IDs per active viewer, fan-out via outbox + worker, author self-insert synchronous) without building it. Mention ranking as a product decision to defer until there's content volume that justifies it.
</details>

**Q2 (scale). "Now 100M users and a few accounts with 30M followers. What breaks?"**
<details><summary>Model answer</summary>

Pure push breaks on celebrity posts: 30M inserts per post means minutes of lag, queue backlog and hot shards. Pure pull breaks on read amplification and scatter-gather across author shards. So: hybrid — push below a follower threshold, pull-and-merge celebrities at read, merge by (ts, id). Gate push to recently active users. Shard feed store by viewer, posts by author. Idempotent writes (zset) since fan-out is at-least-once. Hydration via batched multi-get with a cache; protect the celebrity pull with a cache of their latest posts (it's one hot key read by millions — cache it, and coalesce requests on miss, Day 15). And with ranking, the stored list is a candidate set, with scoring at read time.
</details>

**Q3 (judgment). "What would you NOT build in the first year?"**
<details><summary>Model answer</summary>

Push fan-out, Kafka, ML ranking, a graph database, and a separate feed service. Each solves a problem I can't yet measure. A chronological pull feed with a good index handles tens of thousands of users; I'd rather spend those engineer-weeks on whatever makes the feed worth opening (content, notifications, onboarding). I'd instrument feed latency and follows-per-user distribution from day one, so the migration decision is data-driven rather than fashionable.
</details>

**Q4. "A user blocks someone; the blocked user's posts still show up for an hour. Why, and how do you fix it?"**
<details><summary>Model answer</summary>

Because feed lists are precomputed IDs and I treated the block as a data mutation on lists. Fix: enforce visibility at hydration/read time against the block set (cheap membership check, cacheable per viewer) — correctness at the read boundary, not by scrubbing lists. Background job cleans lists for hygiene only. Add a test that blocks are honoured on every read path, including the celebrity pull path — the second code path is where this regression hides.
</details>

**Q5. "How do you paginate a feed that's changing while the user scrolls?"**
<details><summary>Model answer</summary>

Keyset cursor on (score/timestamp, id), never OFFSET — OFFSET shifts when new items arrive, producing duplicates or skips. Opaque cursor encodes the last-seen pair. New items are surfaced via a "N new posts" pull-to-refresh at the top rather than inserting under the user's thumb. For ranked feeds, the cursor must pin the ranked candidate snapshot (or session id), otherwise re-scoring reorders items between pages.
</details>

**Common mistakes that sink candidates**
- Reciting push/pull/hybrid without doing a single calculation or naming a threshold — it sounds memorised.
- Opening with Kafka/Redis/sharding for a 0→1 prompt. Also: forgetting deletes/blocks and read-your-writes, the two things real users notice first.

## 5. Go further (optional)

- *Designing Data-Intensive Applications*, Ch. 1 ("Describing Load" — the Twitter home-timeline example is exactly today's topic).
- Raffi Krikorian, "Timelines at Scale" (InfoQ, QCon 2013 talk) — the origin of the celebrity/hybrid discussion; search by title.
- Alex Xu, *System Design Interview* Vol. 1, "Design a News Feed System" chapter (sanity-check against, don't memorise).

## Tomorrow

Day 21 — Review + mock: design a news feed / activity stream.
