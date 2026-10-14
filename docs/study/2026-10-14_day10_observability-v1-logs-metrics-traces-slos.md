# Day 10 — Observability v1: logs, metrics, traces; SLOs and error budgets for a five-person team
*Week 2: Reliability · ~60 minutes · 2026-10-14*

**Why this matters in a founding-engineer interview** — At a startup nobody hands you a Datadog contract and an SRE team; you decide what to instrument, what to pay for, and when to wake someone up. Interviewers use observability to test whether you operate systems like an owner: can you answer "is it broken, for whom, since when, why?" in five minutes with a bill that doesn't hurt? You already operate at LinkedIn scale; the signal they want is that you can pick the *minimum* setup for 5 engineers and know what you'd add at 50.

## 1. Primer (10 min)

**Observability** = you can answer questions about system behaviour you didn't predict in advance, using only the telemetry it emits. Monitoring = alerting on questions you *did* predict.

The three signals, by what they're good at:

| Signal | Shape | Best at | Cost driver |
|---|---|---|---|
| Logs | discrete events, text/JSON | "what happened to *this* request/entity" | volume (bytes ingested + retained) |
| Metrics | numeric time series, pre-aggregated | "is it broken / trending", alerting, SLOs | **cardinality** (unique label combos) |
| Traces | causal tree of spans per request | "where did the latency go across services" | span volume (so you sample) |

Key vocabulary:
- **SLI** — a measured ratio of good events / valid events (e.g. requests < 500ms and non-5xx / all requests).
- **SLO** — target for an SLI over a window (99.5% over 28 days).
- **Error budget** — 1 − SLO. 99.5% over 28 days ≈ 3.4 hours of full outage-equivalent (estimate: 28d × 24h × 0.005 = 3.36h).
- **Burn rate** — how fast you're consuming budget relative to "exactly spend it all by window end". Burn rate 1 = on pace to hit the SLO exactly; 14.4 = a 30-day budget gone in ~2 days (14.4 is the figure used in the Google SRE Workbook's multi-window alerting example).
- **RED** (Rate, Errors, Duration) for request-driven services; **USE** (Utilization, Saturation, Errors) for resources (CPU, pool, queue).
- **Structured log / correlation ID** — JSON log lines carrying `request_id`/`trace_id` so one ID joins logs, traces and support tickets.

```
 request ──► API ──► Postgres
   │          │ └──► Queue ──► Worker ──► 3rd-party API
   │          │
 trace_id propagated (W3C traceparent header; copy it into job payloads!)
   │
   ├─ metrics: http_requests_total{route,status}, http_duration_seconds_bucket{route}
   ├─ logs:    {"ts":..,"level":"error","trace_id":..,"tenant":..,"msg":..}
   └─ traces:  span(api) ► span(db) ► span(enqueue) ┄┄► span(worker) ► span(3p call)
```

## 2. Deep dive (25 min)

### 2.1 What to do at "5 users, 5 engineers"
The honest v1 (a few hours of work, near-zero cost):
1. **Structured JSON logs to stdout**, one shared logger config, `request_id` middleware, tenant/user ID on every line. Ship via your platform (Fly/Render/ECS → CloudWatch, or Loki/Grafana Cloud free tier, or Better Stack/Axiom-style hosted logs). Don't self-host Elasticsearch — that's a part-time job.
2. **A handful of RED metrics** per route plus the saturation metrics for things that bite: DB pool in-use, queue depth and *age of oldest job*, outbound-call latency/error per dependency (this is Day 9's bulkhead/breaker telemetry).
3. **Error tracking** (Sentry or equivalent): grouped exceptions with release tags are the best ROI per dollar early on.
4. **An external uptime/synthetic check** hitting a real path (login → read), because your own metrics die with your own box.
5. **OpenTelemetry SDK from day one** if you'll ever want traces: it's vendor-neutral, so you can start with logs-only and turn on tracing without rewriting code. Auto-instrumentation for FastAPI/SQLAlchemy/httpx covers 80% of value.
6. **Two to four SLOs, two to four pages.** That's it.

What you do *not* need yet: a full tracing backend with 100% retention, custom dashboards per service, a metrics pipeline you operate, an on-call rota with escalations.

### 2.2 Cardinality — the cost trap
Metrics are priced and stored per unique label combination. `http_requests_total{route, status}` with 50 routes × 6 status classes = 300 series: fine. Add `user_id` with 100k users = 30M series: a bill and a crash. Rule: **labels are bounded enums** (route *template* `/users/{id}`, not the raw path; status class; region). Unbounded IDs belong in logs and trace attributes. Hosted vendors commonly bill per custom-metric series (list prices on the order of cents per series per month — estimate; check current pricing), so one careless label is a real invoice.

### 2.3 Logs: cheap to write, expensive to keep
Ballpark (estimate): 1 KB per request line × 100 req/s ≈ 8.6 GB/day. At 1M users that's the dominant observability cost. Levers: log at INFO only for state changes and errors, sample success lines, short hot retention (7–14 days) + cold archive in object storage, and *never* log secrets/PII (TOTP seeds, tokens, PAN/Aadhaar-style identifiers — a compliance incident for a fintech or compliance-AI startup).

### 2.4 Traces: sample deliberately
Head sampling (decide at the root, e.g. 1–10%) is simple and cheap but misses rare errors. Tail sampling (collector buffers the trace, keeps all errors and slow ones, samples the rest) captures what you care about but needs a stateful collector — a "Series A" upgrade. For an LLM/agent product, traces *are* the product's debugger: each agent step/tool call/LLM call is a span with prompt hash, token counts, latency and cost. That is the same data an eval/replay system needs, so instrument it once and reuse it.

### 2.5 SLOs for a five-person team
Pick SLIs from the user's perspective, not the host's:
- **Availability**: good = non-5xx on `POST /orders` (excluding client 4xx), valid = all such requests.
- **Latency**: good = responses under 500 ms (use a histogram threshold, *not* average).
- **Freshness/correctness** (data products): e.g. "market tick lag p99 < 2 s during market hours".
- **Async work**: fraction of webhooks delivered within 60 s (Day 7's service).

Choose the SLO from what users tolerate and what you can sustain, not from nines vanity. 99.9% = 43 min/month of budget (estimate: 30d × 24 × 60 × 0.001 = 43.2 min); with 5 engineers and no on-call rotation, 99.5–99.9% on a couple of critical paths is honest. **The error budget is a decision tool**: budget left → ship faster; budget burned → freeze risky launches and spend the sprint on reliability. At a startup, the real value is giving the founder a non-emotional way to trade features for stability.

**Alerting that doesn't destroy a small team**: page only on *symptoms with urgency* (user-visible SLO burn), not causes (CPU 85%). The classic pattern is multi-window burn-rate: page when burn is high over a long and short window together (e.g. 14.4× over 1 h *and* 5 min → ~2% of a 30-day budget gone in an hour); ticket when slow burn (e.g. 1× over 3 days). Every page must be actionable; if a page is routinely ignored, delete it. Prefer ~1–2 pages/week/person as an upper bound for sanity.

### 2.6 Scale changes: 5 users → 1M users
| Concern | 5 engineers | 1M users / 50 engineers |
|---|---|---|
| Logs | hosted, 7-day hot | tiered storage, sampling, per-team quotas |
| Metrics | managed Prometheus/vendor, ~30 series per service | cardinality governance, recording rules, exemplars linking to traces |
| Traces | auto-instr., head sample 5–10% | tail sampling, service maps, SLO-linked exemplars |
| SLOs | 2–4, one dashboard | per-service SLOs, ownership, error-budget policy signed by eng + product |
| On-call | founders + best-effort | rota, runbooks, escalation, postmortem process (Day 12) |
| Build vs buy | **buy** almost everything | buy/build hybrid driven by bill; OTel keeps you portable |

At LinkedIn the instinct is a platform team owning this; the founding-engineer move is to say plainly that you'd *rent* it and keep the code portable via OpenTelemetry.

### 2.7 Worked example: your NSE feed
The incident where 1,426 reconnects got the client IP blocked is an observability story as much as a retry story: what telemetry would have caught it at reconnect #5 rather than #1,426?
- Counter `feed_reconnects_total{reason}` and an alert on rate (e.g. > 5 in 10 min) — the symptom page.
- Gauge `feed_last_tick_age_seconds` per feed — the SLI behind "freshness: p99 tick age < 2 s in market hours".
- Counter `http_429_total{endpoint}` and a gauge for the current penalty-ladder step — the saturation signal.
- A structured log line on each reconnect with `attempt`, `backoff_s`, `reason`, so post-incident you can reconstruct the storm from logs alone.

Small instrumentation sketch (Python; prometheus_client + structured logging):

```python
import logging, time, json
from prometheus_client import Counter, Gauge, Histogram

RECONNECTS = Counter("feed_reconnects_total", "Feed reconnects", ["reason"])      # bounded enum
TICK_AGE   = Gauge("feed_last_tick_age_seconds", "Seconds since last tick")
CALL_LAT   = Histogram("upstream_call_seconds", "Upstream latency", ["dep"],
                       buckets=(.05, .1, .25, .5, 1, 2.5, 5))

log = logging.getLogger("feed")

def on_reconnect(reason: str, attempt: int, backoff_s: float):
    RECONNECTS.labels(reason=reason).inc()
    log.warning(json.dumps({"event": "reconnect", "reason": reason,
                            "attempt": attempt, "backoff_s": backoff_s}))
```
Note `reason` is a bounded enum (`"timeout"|"429"|"server_close"|...`), never an exception string.

## 3. Exercise (15 min)
**Task: Write `OBSERVABILITY.md` (one page) for a hypothetical or real v1 you know** — default to the webhook delivery service from Day 7, or your NSE feed system if you prefer. Timebox: 15 minutes, pen or keyboard.

It must contain:
1. **Three SLIs/SLOs** (each: SLI definition as good/valid, target, window, resulting budget in minutes or events).
2. **A metrics list** — at most 10, each with its label set and an explicit statement that every label is bounded.
3. **Two pages and two tickets** — each written as a burn-rate or symptom condition, with one sentence on the first action the responder takes.
4. **One "we are deliberately not doing this yet"** (e.g. tail sampling, per-tenant dashboards) with the trigger that would make you do it.

"Done" = a teammate who has never seen the system could set up the dashboard and alerts from your page, and you can defend each label's cardinality in one sentence.

## 4. Interview drill (10 min)

**Q1 (0→1). You're the first engineer. What observability do you set up in week one?**
<details><summary>Model answer</summary>
Structured JSON logs with a request ID and tenant ID, RED metrics per route plus saturation (DB pool, queue depth and oldest-job age, per-dependency latency/errors), Sentry-style error tracking tagged with release, and an external synthetic check on one real user journey. I'd add the OpenTelemetry SDK now with auto-instrumentation so tracing is a config switch later, but I wouldn't run my own backend. Then define 2–3 SLOs on the revenue-critical paths and a single dashboard that answers "is it broken, for whom, since when". Total effort is about a day, cost mostly free tiers. What I'd skip: custom dashboards per service, self-hosted ELK, anything with an on-call rota while we're five people.
</details>

**Q2 (scale). Your observability bill tripled in a quarter as traffic grew 2×. What do you look at?**
<details><summary>Model answer</summary>
Break the bill down by signal first. Usually it's either log volume or metric cardinality. For logs: top emitters by service/line, drop or sample success-path INFO, shorten hot retention, move bulk to object storage. For metrics: find the series count by metric and by label; an unbounded label (user ID, raw URL, exception message) is the typical culprit — fix by templating routes or moving the ID into logs/trace attributes. For traces: move from head sampling to tail sampling, keeping all errors and slow traces and a small sample of the rest. Then set per-team budgets and an alert on ingestion rate so it's caught in days, not at invoice time. Growth of 2× causing 3× cost implies something superlinear — cardinality — so I'd look there first.
</details>

**Q3 (judgment). Would you set a 99.99% SLO to impress customers?**
<details><summary>Model answer</summary>
No. 99.99% allows about 4.3 minutes of downtime a month (estimate). With a small team, a single deploy gone wrong or a cloud-provider blip consumes it, and the engineering required (multi-region, automated failover, zero-downtime migrations) is a different company's roadmap. I'd start where we can actually hold the line — say 99.5–99.9% on the critical path — publish it internally, and tighten only when customers or contracts demand it and we've bought the capability. An SLO you routinely miss is just noise; one you meet with large headroom means you're over-investing. The budget is a prioritisation tool, not a marketing number.
</details>

**Q4. Latency suddenly doubled for some customers only. How does your telemetry help you find out why?**
<details><summary>Model answer</summary>
Metrics tell me it's real and when it started (latency histogram p95/p99 by route; correlate with deploy markers). To find *who*, I need a high-cardinality dimension: query logs or trace attributes by tenant/region/plan, not metrics labels. Then exemplars or a sampled trace from an affected tenant shows which span grew — DB query, a downstream call, or queue wait. Typical findings: one tenant's data shape hits a bad query plan, a noisy neighbour exhausting a shared pool (Day 9 bulkhead), or one dependency region degraded. This is why tenant ID must be in every log line and trace but not in metric labels.
</details>

**Q5. How do you trace a request that goes through a queue and a worker?**
<details><summary>Model answer</summary>
Context doesn't propagate across async boundaries automatically. I'd inject the W3C `traceparent` into the job payload or message headers at enqueue time and extract it in the worker to start a child span (or a linked span, since the worker may run minutes later and a "link" models that more honestly than a parent/child). Also record queue wait time as its own metric/span attribute, since "slow" is often time spent waiting, not working. Same trick for the outbox pattern from Day 6: store the trace context in the outbox row.
</details>

**Common mistakes that sink candidates**
1. Naming tools (Prometheus, Grafana, Jaeger) without saying what questions they answer, what they cost, or what you'd skip at five engineers — or proposing a full self-hosted stack for a team that can't staff it.
2. Alerting on causes (CPU, memory) and averages instead of user-visible symptoms and percentiles; or putting user IDs in metric labels. Both signal you haven't run a real system with a bill and a pager.

## 5. Go further (optional)
- Google SRE Workbook, "Implementing SLOs" and "Alerting on SLOs" chapters (sre.google/workbook) — the source of the multi-window, multi-burn-rate alerting pattern.
- Charity Majors, Liz Fong-Jones, George Miranda, *Observability Engineering* (O'Reilly) — high-cardinality events vs metrics argument.
- OpenTelemetry documentation, "Concepts" and "Context propagation" (opentelemetry.io/docs). Links not fetched in this run; verify paths on site.

## Tomorrow
Day 11 — Long-lived connections: heartbeats, liveness detection, reconnect, gap backfill.
