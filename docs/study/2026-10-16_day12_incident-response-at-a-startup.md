# Day 12 — Incident response at a startup: on-call, postmortems, and your incident-command STAR story
*Week 2: Reliability · ~60 minutes · 2026-10-16*

**Why this matters in a founding-engineer interview** — As employee #1–3 you *are* the on-call rotation, the incident commander and the postmortem author, and founders probe this because an outage in month four can cost the one design partner you have. Interviewers aren't looking for "I never break things"; they want evidence that you stay calm, communicate early, fix the cause rather than the symptom, and leave the system better. Your 1,426-reconnect IP-block incident is a rare thing: a real incident where you were detector, responder, commander and author at once.

## 1. Primer (10 min)

**Core mental model: an incident is a coordination problem first and a debugging problem second.** At scale, big-company incidents fail on communication (who owns it? who told the customer?). At a startup they fail on the opposite: one person, adrenaline, no notes, a fix made at 2 a.m. that nobody understands at 9 a.m.

**Vocabulary**
- **Severity (SEV)**: a pre-agreed label that decides *how loudly* you respond. Keep it to three levels at a startup (below).
- **Incident Commander (IC)**: the person who coordinates, not necessarily the person typing the fix. With one engineer, you wear both hats, but *write down* which hat you're wearing.
- **Mitigate vs resolve vs root-cause**: mitigate = stop the bleeding (rollback, flag off, shed load); resolve = service healthy; root cause = why it happened. Mitigate first, always. Root-causing live is how 20-minute incidents become 3-hour ones.
- **Timeline**: timestamped log of observations and actions, kept *during* the incident.
- **Blameless postmortem**: analysis that asks "what about the system let this happen / made it hard to detect?" rather than "who did it?". Blameless ≠ accountability-free: actions still have owners and dates.
- **MTTD / MTTM / MTTR**: time to detect / mitigate / recover. For a small team, **MTTD is usually your worst number** because nobody is watching.
- **Error budget / SLO**: from Day 10; the budget is the trigger for "freeze features, fix reliability".

**Three severities that are enough for a 5-person company (suggested defaults, tune to the product)**

| SEV | Meaning | Response |
|---|---|---|
| 1 | Core flow down or data loss/corruption/security exposure, for most users | Page immediately, any hour; customer comms within 30 min |
| 2 | Major degradation or one important customer broken | Respond within an hour in waking hours; comms within a few hours |
| 3 | Minor bug, workaround exists | Ticket, normal planning |

**Lifecycle diagram**

```
detect ─▶ triage (sev?) ─▶ declare ─▶ mitigate ─▶ verify ─▶ communicate (throughout)
                                                    │
                          resolve ◀─────────────────┘
                              │
                    postmortem (≤ 5 working days) ─▶ action items ─▶ tracked to done
```

## 2. Deep dive (25 min)

### 2.1 On-call at a startup: design it before you need it

**With one to three engineers, "on-call" is mostly a promise about detection, not a rota.** The honest founding-engineer position:

1. **Page on symptoms, not causes.** Page when users are hurt (error rate, latency SLO burn, "no data for N minutes") — not on CPU at 80%. A rule of thumb from the Google SRE book: every page should be actionable, urgent, and require human intelligence. If it fails any of those, it's a ticket or a dashboard.
2. **Burn-rate alerts beat static thresholds** (Day 10). Example: page if you're burning the 30-day error budget at 14× for 5 min *and* 1 hour (this is the multi-window pattern in the SRE Workbook; the exact numbers are a starting point, not gospel).
3. **Dead-man's switch / heartbeat monitor.** The scariest failure is silence: the alerting pipeline is down, or the cron stopped. Have something external (a cheap uptime check, a healthchecks-style "ping me every N min or alert") that pages on *absence*. Your market-data feed is the textbook case: "no ticks for 60 s during market hours" is a better alert than any error counter, because a dead feed emits no errors.
4. **Alert budget.** More than ~1–2 actionable pages per on-call week at a tiny team means you're burning out the only people who can fix things. Delete or demote noisy alerts in the postmortem.
5. **Humane rotation.** With 2 engineers: weekly swap, an explicit "off" day after a night page, and a written rule that the founder (not the engineer) decides about waking someone for SEV2. Sleeping engineers write fewer outages.
6. **Runbooks: the first three lines matter most.** For each page: what it means, how to confirm it, the one-command mitigation (rollback, flag, restart). Long runbooks go stale; short ones get used.

**Tooling at 5 users vs 1M users (estimates; pricing varies)**

| Stage | Detection | Paging | Comms | Postmortem |
|---|---|---|---|---|
| Seed / 5 users | Uptime check + error tracker (Sentry-class) + 3 SLO alerts | Phone push/SMS via a free-tier pager or even a Slack→phone rule | A pinned `#incidents` channel, manual status note to customers | One page of Markdown in the repo |
| Series A / ~50k users | Metrics + tracing, burn-rate alerts | Real paging tool, a rota of 4–6 | Public status page, templated updates | Review meeting for SEV1/2 |
| 1M users | Per-service SLOs, synthetic probes, anomaly detection | Follow-the-sun, dedicated IC role training | Comms lead separate from IC | Org-wide incident review, action-item SLAs |

The interview point: **don't buy a $30/seat/month incident platform for three people**, but do own the *behaviours* (declare, timeline, mitigate-first, postmortem) on day one. Behaviours are free.

### 2.2 During the incident: a protocol you can run exhausted

Write this on one page. Run it literally:

1. **Declare.** Post "SEV? — <symptom> — I'm IC, investigating" in the incident channel *within minutes*. Declaring costs nothing; silence costs trust.
2. **Start the timeline.** One line per event: `14:02 alert fired`, `14:05 suspect deploy 3f2a`, `14:09 rolled back`. This becomes 70% of the postmortem.
3. **Ask "what changed?"** Deploys, config, flags, dependency/vendor status, traffic shape, certificates, quotas, clocks. Most incidents are a change in the last hour.
4. **Mitigate with the smallest reversible action.** Rollback > flag off > scale up > restart > hotfix. A hotfix written live is the highest-risk option; prefer anything that returns you to a known state.
5. **Communicate on a clock.** Every 30 min for SEV1 even if the update is "no change, still investigating". Say what users see, what you're doing, next update time. Don't guess at causes publicly.
6. **Verify recovery with the same signal that detected it**, then watch for 15–30 min before standing down. Premature "all clear" followed by recurrence is worse than a late one.
7. **Preserve evidence before you clean up.** Snapshot logs, capture the bad state, note the commit. Restarts destroy forensic state; take a copy first when it's cheap.

**Single-engineer failure modes** worth naming in an interview: tunnel vision (fixating on one hypothesis — set a 15-min timer, then change approach), fix-forward under stress (introducing a second bug), and not telling anyone (the founder learns from a customer).

### 2.3 Postmortems that change something

A good postmortem is a **short, specific, owned** document. Skeleton:

```markdown
# <date> — <one-line impact>
Severity / Duration / Detection (alert? customer?) / MTTD / MTTM
## Impact          (who, how many, for how long, data affected?)
## Timeline        (timestamps, UTC or IST — say which)
## Root cause(s)   (the chain, not the culprit; 5-whys until you hit a system property)
## What went well / badly / lucky
## Action items    (each: owner, due date, type: prevent | detect | mitigate)
```

Practices that separate real postmortems from theatre:
- **Contributing factors, not "the root cause."** Real incidents have a chain: trigger + latent condition + missing safeguard. "Human error" is never a root cause; it's where analysis starts ("why was it possible to do that without a guard?").
- **Action items sorted by type.** *Prevent* (rarely fully possible), *detect faster* (usually the best ROI at a startup), *mitigate faster* (rollback, kill switch). Limit to 3–5 items you will actually finish; 20 items is a graveyard.
- **Track to done.** Close the loop by reviewing last month's actions at the start of the next postmortem. This is the part big companies are bad at and the part a founding engineer can do cheaply.
- **Write it within a week** while memory is fresh; share it externally (a shortened version) if customers were affected — a candid, specific note usually *increases* trust.
- **Test the fix.** This is your essay: "the code that handles failure is the code nobody tests." The action item isn't "add a gate"; it's "add a gate **and a test that simulates the failure that triggered it**". A fix with no regression test is a promise.

### 2.4 Worked example: your NSE incident, structured as an IC would

Facts you can verify from your own logs; fill in exact numbers before an interview rather than trusting memory.

- **Trigger**: a reconnect loop against the Dhan websocket; **1,426 reconnects** → client IP blocked.
- **Chain**: transient disconnect → naive immediate reconnect → each failed/short-lived connect retried with no global gate → server-side rate protection treats you as abusive → *block*, which turns a blip into a total outage for your IP. (Day 8's retry-storm shape; here the amplifier was your own client.)
- **Detection** (be honest about which it was): if you noticed from missing ticks, say so — it's a good MTTD story *because* it motivates the dead-man's alert.
- **Mitigation**: stop the loop first (kill switch), *then* investigate. Switching to a different egress IP or waiting out the block are mitigations, not fixes.
- **Fixes**: an anti-reconnect-storm gate (global limiter across all reconnect paths, not per-connection), 429 penalty backoff ladder, jittered delays.
- **Detect/mitigate-faster items**: alert on reconnect-rate, "no ticks for N s in market hours", a circuit-breaker state that you can see on a dashboard.
- **Test**: a fake server that drops the connection repeatedly asserts reconnect attempts per minute stay below a cap.

Sketch of the gate (kept deliberately small — the story is the reasoning, not the code):

```python
import time

class ReconnectGate:
    """Global cap across ALL reconnect paths. Ladder grows on penalty (e.g. HTTP 429)."""
    LADDER = [5, 15, 60, 300, 900]            # seconds; illustrative values

    def __init__(self, max_per_window=5, window=60, clock=time.monotonic):
        self.max, self.window, self.clock = max_per_window, window, clock
        self.attempts: list[float] = []
        self.rung = 0
        self.blocked_until = 0.0

    def allow(self) -> bool:
        now = self.clock()
        if now < self.blocked_until:
            return False
        self.attempts = [t for t in self.attempts if now - t < self.window]
        if len(self.attempts) >= self.max:
            return False
        self.attempts.append(now)
        return True

    def penalise(self):                        # call on 429 / block signals
        self.blocked_until = self.clock() + self.LADDER[min(self.rung, len(self.LADDER)-1)]
        self.rung += 1

    def success(self):                         # sustained health only, not a single connect
        self.rung = 0
```

Note the injectable `clock`: that's what makes the failure-path test cheap and deterministic.

**Say "submitted patches" about your OSS work; don't claim merged.** A brief tie-in that fits incident narratives: pykiteconnect #237 (ping loop that never pings, regression hidden in a commit titled "downgrade urllib3") is an *incident-forensics* story: a symptom (silent staleness) traced via `git log`/bisect to a mislabeled commit. Use it as evidence for "I find the change that caused it."

### 2.5 The STAR story: structure and traps

**S**ituation (2 sentences, stakes) → **T**ask (your specific role: "I was the only engineer, so detection, response and follow-up were mine") → **A**ction (4–5 concrete verbs, in order, with *why*) → **R**esult (numbers + what changed afterwards + what you'd do differently).

Traps: spending 80% on Situation; "we" for everything (interviewers can't hire "we"); a result with no durable change; blaming the vendor (the vendor blocking you was predictable behaviour — your gate was the missing safeguard). The strongest closing line is a *process* change: "and it's why every reconnect path now goes through one gate with a test."

## 3. Exercise (15 min)

**Task: write your incident-command STAR story and its one-page postmortem, in that order, timed.**

1. (8 min) Draft a postmortem of the 1,426-reconnect incident using the skeleton above. Use real numbers from your logs/repo history where you have them; mark anything you can't verify with `[verify]`. Include exactly **3 action items**, one each of *prevent / detect / mitigate*, each with owner "me" and a date.
2. (5 min) Compress it into a STAR story of **~90 seconds spoken** (≈220 words). Read it aloud once, timing yourself.
3. (2 min) Add a one-sentence answer to "what would you do differently?" that is *not* "nothing" and *not* "write more tests."

**Done =** a Markdown file with the postmortem (all 7 headings filled), a 220-word STAR script, every number either verified or tagged `[verify]`, and no sentence in which a vendor, a library or "unforeseen circumstances" is the root cause. Save it where you'll find it (suggestion: your own notes, not this repo).

## 4. Interview drill (10 min)

**Q1 (behavioural + 0→1): "Tell me about a production incident you handled. What was your role?"**
<details><summary>Model answer</summary>

Use the 90-second STAR. Shape: "I run a real-time NSE market-data system solo. During market hours our websocket client got into a reconnect loop — 1,426 attempts — and the broker blocked our IP, so we were blind. I (1) stopped the loop immediately with a kill switch, (2) confirmed from logs that every reconnect path bypassed any shared limiter, (3) restored data via a mitigation [fill in: waiting out the block / alternate route], (4) shipped a global reconnect gate with a penalty-backoff ladder for 429s, and (5) wrote a test with a fake server that flaps the connection and asserts attempts-per-minute stay under the cap. Result: [number]. The durable change: failure paths now have the same test bar as the happy path." Close with the detection gap: "I found out from missing data, so I added a no-ticks-in-market-hours alert."
</details>

**Q2 (judgment): "You're on-call alone at 2 a.m., checkout error rate is 40%, and you've found a likely bug in last night's deploy. Rollback or hotfix?"**
<details><summary>Model answer</summary>

Rollback, unless the deploy included a destructive migration that can't be reversed (then assess forward-fix risk explicitly). A rollback returns to a known-good state in minutes and is rehearsed; a hotfix at 2 a.m. is untested code written by a tired person. Declare the incident, post the first update, roll back, verify with the error-rate signal that paged me, then root-cause in daylight and re-ship the fix through normal review with a test. I'd also check whether the migration/schema is backward-compatible — which is the design reason to do expand/contract migrations in the first place.
</details>

**Q3 (scale): "Your team grows from 3 to 30 engineers and incidents are chaotic. What do you change?"**
<details><summary>Model answer</summary>

Separate roles: IC (coordinates), ops lead (hands on keyboard), comms lead (customers/internal). Introduce service ownership and a rota per area rather than one global rota; severity definitions tied to SLOs; a status page with templated updates; incident review for SEV1/2 with action-item tracking and due dates; and invest in detection (per-service burn-rate alerts) and mitigation primitives (feature flags, one-click rollback, load-shed switches). Train people on the IC role through shadowing. I'd measure MTTD/MTTM trends and action-item completion rate, not incident count — fewer *reported* incidents can just mean worse detection.
</details>

**Q4 (what would you NOT build): "We have 4 engineers. Should we set up PagerDuty, a status page, a chaos-engineering programme and a formal incident-review board?"**
<details><summary>Model answer</summary>

Not all four. Now: external uptime/dead-man's monitoring, three SLO-based alerts to a phone, a one-page protocol, a postmortem template, and a pinned incident channel. Soon (when a customer asks or SEV frequency justifies): a paging tool and a basic status page. Not yet: chaos engineering (you can't learn from injected failures if you can't see baseline behaviour) and a review board (a 30-minute review with the whole team is enough). The criterion: does it reduce MTTD or MTTM for the failures we actually have? I'd rather run one game-day — kill the DB connection in staging and see who notices — than buy a platform.
</details>

**Q5 (probe): "Tell me about a time you made a mistake in production."**
<details><summary>Model answer</summary>

Pick an incident where the fault was yours, own it in the first sentence, spend most of the time on the mechanism and the systemic fix. Your reconnect gate works: "my client was the amplifier." Avoid a fake-weakness story; avoid blaming the broker. End with the changed practice and the regression test.
</details>

**Common mistakes that sink candidates**
1. **Hero narrative**: "I stayed up 20 hours and fixed it" — signals you'll be a single point of failure. Show that you *reduced* the need for heroics.
2. **Root cause = a person or a tool** ("a junior pushed bad code", "AWS had an outage") with no discussion of missing guards, detection or blast-radius limits. Also: no mention of customer communication at all.

## 5. Go further (optional)

- *Site Reliability Engineering* (Google, free online at sre.google/sre-book): "Managing Incidents", "Postmortem Culture: Learning from Failure", "Being On-Call".
- *The Site Reliability Workbook*: chapter "Alerting on SLOs" (multi-window, multi-burn-rate alerts) and the incident-response chapter.
- PagerDuty's open-source incident-response documentation (response.pagerduty.com) — a free, practical template for roles and comms. (I haven't verified the URL loads from this environment; search the title if it moves.)

## Tomorrow

Day 13 — Practical drill: webhook receiver with signature verification, retries and dedupe.
