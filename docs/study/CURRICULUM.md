# Founding Engineer Interview Curriculum — 12 weeks, 1 hour/day

**Started:** 2026-10-05 · **Delivered by:** the `founding-engineer-daily-study` cloud routine (claude.ai) at 8:00 AM IST, one session per day, committed to the `study` branch.
**Lessons land in:** `docs/study/YYYY-MM-DD_dayNN_<slug>.md` · **Progress:** `docs/study/PROGRESS.md` · **Steer it:** `docs/study/NOTES.md`

## How the plan is built

Founding-engineer loops test five things, and the weeks are weighted to match:

| Round | What it tests | Weeks |
|---|---|---|
| 0→1 system design | Sequencing, scope-cutting, "what I'd refuse to build yet" | 1, 7, 9 |
| At-scale system design | That you've seen what breaks — your LinkedIn war stories | 2, 3, 8, 9 |
| Practical build (90 min) | Ship a working thing alone, narrate the cuts | one drill most weeks, 10 |
| Product sense + founder chemistry | Product judgment, why-you, spiky opinions | 5, 11 |
| Leadership / team-building | Can you hire and lead engineers #2–#5 | 6 |

Week 4 (AI/agent systems) is weighted heavily on purpose: ~8 of 13 founding-engineer JDs sampled in Aug 2026 hired for agent reliability, evals or observability.

**Every 7th day is a review + mock** on the week's material. After Day 84 the routine switches to spaced review of your weakest topics (from `NOTES.md`).

**Session shape (60 min):** 10 min primer → 25 min deep content → 15 min exercise → 10 min interview Q&A with model answers.

---

### Week 1 — The game, and 0→1 design foundations
1. What founding engineers are actually evaluated on; the loop shape; your one-breath narrative
2. The 0→1 design method: requirements → v1 monolith + Postgres → sequencing → what you'd refuse to build
3. Postgres for startups: indexes, transactions and isolation levels, when Postgres is enough
4. Practical drill: a rate limiter in 90 minutes (token bucket vs sliding window) + DECISIONS.md
5. API design: REST vs RPC, idempotency keys, pagination, versioning
6. Background jobs and queues: when to add one, at-least-once delivery, idempotent consumers, the outbox pattern
7. **Review + mock:** design v1 of a webhook delivery service

### Week 2 — Reliability (your essay's theme)
8. Timeouts, retries, backoff and jitter; retry storms (your 1,426-reconnect incident as the story)
9. Circuit breakers, bulkheads, load shedding, backpressure
10. Observability v1: logs, metrics, traces; SLOs and error budgets for a five-person team
11. Long-lived connections: heartbeats, liveness detection, reconnect, gap backfill
12. Incident response at a startup: on-call, postmortems, and your incident-command STAR story
13. Practical drill: webhook receiver with signature verification, retries and dedupe
14. **Review + mock:** real-time notification system, 0→1 then to 1M users

### Week 3 — Data at scale (Register A: your LinkedIn war stories)
15. Caching: cache-aside, invalidation, stampedes, TTL strategy
16. Replication and consistency: leader/follower, read-your-writes, CAP/PACELC in practice
17. Partitioning and sharding: key choice, hotspots, resharding
18. Event streams: the Kafka model, ordering, consumer groups, the exactly-once myth
19. Workshop: turn three LinkedIn projects into quantified scale war stories
20. Feeds: fan-out on write vs fan-out on read
21. **Review + mock:** design a news feed / activity stream

### Week 4 — AI and agent systems engineering
22. LLM application architecture: context, retrieval basics, latency and cost budgets
23. Agents in production: tool calls, loops, state, durable execution
24. Evals: offline vs online, LLM-as-judge pitfalls, regression suites
25. Tracing and deterministic replay for agents; turning a production failure into a test
26. Cost control and multi-tenancy for LLM features: per-tenant budgets, caching, model routing
27. Practical drill: a minimal eval harness in 90 minutes
28. **Review + mock:** design an AI support agent with evals and guardrails

### Week 5 — Product sense and founder chemistry
29. How founders think: product-market fit and the metrics that matter at seed
30. The two-hour company teardown method
31. Scope-cutting: MVPs, feature flags, kill criteria
32. Engineers reading data: instrumentation, funnels, talking to users
33. Your "why" answers: why leave LinkedIn, why a startup, why this domain — write and rehearse
34. Three spiky, defensible engineering opinions
35. **Review + mock:** a founder chemistry call

### Week 6 — Building and leading the team
36. Hiring engineers #2–#5: sourcing, the bar, loop design
37. Write your Hiring Plan one-pager
38. Onboarding and culture at five people: the minimum viable process
39. Workshop: mentoring and leadership STAR stories
40. Technical decision-making: lightweight RFCs/ADRs, disagree-and-commit
41. Managing tech debt deliberately at a startup
42. **Review + mock:** behavioral / leadership loop

### Week 7 — Infrastructure for small teams
43. Cloud choices: PaaS vs raw AWS, managed services, cost discipline
44. CI/CD, trunk-based development, feature flags, safe deploys and rollback
45. Containers, and when not to use Kubernetes
46. Security fundamentals: authn/authz, secrets, OWASP risks, multi-tenant isolation
47. Privacy for engineers: DPDP and GDPR basics, PII handling
48. Practical drill: plan and ship FastAPI + Postgres with CI in 90 minutes
49. **Review + mock:** design a multi-tenant B2B SaaS backend

### Week 8 — Fintech and real-time (your scar tissue)
50. Payments fundamentals: ledgers, double-entry, idempotency, reconciliation
51. Designing a ledger service
52. Real-time market-data pipelines: ingestion, normalization, fan-out
53. UPI and multi-gateway reconciliation in India
54. Audit trails and compliance-grade logging
55. Practical drill: a double-entry ledger with enforced invariants
56. **Review + mock:** design a wallet / payment system

### Week 9 — High-frequency interview designs
57. URL shortener and distributed ID generation
58. Distributed job scheduler
59. Chat and messaging
60. File upload, storage and media pipelines
61. Distributed rate limiting
62. Collaborative editing: OT vs CRDT overview
63. **Review + mock:** one of the above, interviewer's choice

### Week 10 — Coding round sharpening
64. Patterns: hash maps, two pointers, sliding window
65. Graphs: BFS, DFS, topological sort
66. Heaps and intervals
67. Python concurrency: asyncio, threads, the GIL, race conditions
68. Practical drill: LRU cache with TTL, plus tests
69. The code-review round: how to review and narrate
70. **Review + mock:** practical build round

### Week 11 — Equity, offers and diligence
71. Equity mechanics: options, fully diluted %, vesting, exercise windows, dilution
72. India ESOP taxation and modelling outcomes
73. Reverse diligence: questions, references, red flags
74. Negotiating a founding role
75. Reading a startup: runway, burn, cap-table basics
76. Build your decision scorecard
77. **Review**

### Week 12 — Integration and full mocks
78. Full mock: founder conversation
79. Full mock: 0→1 design
80. Full mock: at-scale design
81. Full mock: practical build
82. Full mock: behavioral and leadership
83. Gap review: your three weakest topics
84. Capstone: your pitch, story bank, and 30-60-90-day plan as a founding engineer
