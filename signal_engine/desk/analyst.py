"""The reasoning layer: deterministic rule-based reviewer + an OPTIONAL single LLM call.

Two hard rules, both structural rather than aspirational:

1. **The deterministic path must produce a useful review with zero API calls.** The nightly job
   can never break because a key expired, and can never surprise-bill. Everything below —
   findings, hypotheses, proposals, the narrative — is generated from `facts`/`attribution`/`gap`
   with no network. The LLM layer is additive prose only; if it fails, the review is unchanged.
2. **The desk proposes; it never applies.** `FORBIDDEN_PROPOSAL_KEYS` additionally hard-blocks
   the three things the human has explicitly ring-fenced (the live P0 gate value, the universe
   allowlist, the movers honesty labels) so a future night cannot "helpfully" suggest reversing a
   settled verdict.

When `SE_DESK_LLM=1`, the LLM discipline follows the patterns validated in
docs/TRADINGAGENTS_ANALYSIS_2026-07.md §5 S2 / Appendix B:

* **pre-fetch every fact deterministically, then ONE structured call** — never a tool-calling
  loop. Their documented bug (#984) was an LLM asked to analyse sources it had no tool for, which
  fabricated them wholesale.
* **a verified-snapshot block** listing every number the prose may cite, computed in code, with
  the instruction that discrepancies must be FLAGGED, not reconciled.
* **an instrument-identity anchor** — NSE symbols are ambiguous (IDEA, the Jindal family), and
  their issue #814 was an LLM silently substituting a different company mid-run.
* **structured output with a graceful fallback** to the deterministic report.
* **a quick/deep model split and a ₹ cost meter** (`signal_engine/obs/llm_cost.py`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from signal_engine.desk.attribution import (
    EDGE_VERDICT_BAR, MIN_SESSIONS_FOR_LIVE_CLAIM, Finding,
)
from signal_engine.obs.llm_cost import CostMeter

# Model ids verified against the Claude API model table on 2026-07-26 (see the `claude-api`
# skill). Quick model does the prose; the deep model is reserved for the one judgement-heavy
# call, mirroring the 9-of-11-roles-on-the-cheap-model ratio that TradingAgents got right.
QUICK_MODEL = os.getenv("SE_DESK_LLM_QUICK", "claude-haiku-4-5")
DEEP_MODEL = os.getenv("SE_DESK_LLM_DEEP", "claude-opus-5")

# Ring-fenced by the human. A proposal touching any of these is dropped with a logged reason.
FORBIDDEN_PROPOSAL_KEYS = (
    "risk.max_cost_r",                      # the LIVE P0 friction-in-R gate
    "live_universe.restrict_to_allowlist",  # killed three times by data; stays false
    "movers.labels",                        # the movers sleeve's honesty labels
)


@dataclass
class Hypothesis:
    """A nightly hypothesis with its pre-registered test written BEFORE any grading."""

    statement: str
    rationale: str
    pre_registered_test: str
    falsifiable_prediction: str
    metric: str
    direction: str
    threshold: float
    min_sessions: int
    min_n: int


@dataclass
class Proposal:
    """A written diff plus its evidence bar. NEVER applied by the desk."""

    target_file: str
    config_key: str
    current_value: str
    proposed_value: str
    diff: str
    rationale: str
    evidence_bar: str
    expected_effect: str
    effort: str
    rank: int


@dataclass
class AnalystOutput:
    narrative: List[str] = field(default_factory=list)
    hypotheses: List[Hypothesis] = field(default_factory=list)
    proposals: List[Proposal] = field(default_factory=list)
    kill_criteria: List[str] = field(default_factory=list)
    llm_commentary: Optional[str] = None
    llm_model: Optional[str] = None
    cost: CostMeter = field(default_factory=CostMeter)
    dropped_proposals: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------- #
# Deterministic reviewer
# --------------------------------------------------------------------------------------- #

def review_deterministic(facts, att, gaps: Dict, dead_ideas: List[str]) -> AnalystOutput:
    """Generate narrative, hypotheses and proposals from the facts. No LLM, no network."""
    out = AnalystOutput()
    session_gap = gaps.get("session")
    # The gated-era window describes the engine as it is CONFIGURED TODAY; the full trailing
    # window spans the pre-P0 regime. Prefer gated for any verdict, and fall back only if the
    # gated window is empty.
    trailing_gap = gaps.get("gated") or gaps.get("trailing")

    if not facts.integrity.usable:
        out.narrative.append(
            "**REFUSING TO CONCLUDE.** Session integrity verdict is {}: {}. No performance, "
            "strategy or gap conclusion may be drawn from this day, and none is drawn below. "
            "The only actionable items are operational.".format(
                facts.integrity.verdict, "; ".join(facts.integrity.reasons)))
        out.proposals.extend(_ops_proposals(facts))
        out.kill_criteria = _kill_criteria(facts, trailing_gap)
        _filter_and_rank(out)
        return out

    out.narrative.extend(_narrative(facts, att, session_gap, trailing_gap))
    out.hypotheses.extend(_hypotheses(facts, att, dead_ideas))
    out.proposals.extend(_proposals(facts, att, trailing_gap))
    out.proposals.extend(_ops_proposals(facts))
    out.kill_criteria = _kill_criteria(facts, trailing_gap)
    _filter_and_rank(out)
    return out


def _filter_and_rank(out: AnalystOutput) -> None:
    """Drop ring-fenced keys, then renumber ranks 1..n contiguously.

    Runs on BOTH paths (usable and dead session) so a report never shows a lone "P4".
    """
    kept: List[Proposal] = []
    for p in out.proposals:
        if any(p.config_key.startswith(k) for k in FORBIDDEN_PROPOSAL_KEYS):
            out.dropped_proposals.append(
                "DROPPED proposal touching ring-fenced key {} — the human has explicitly "
                "excluded it from desk proposals.".format(p.config_key))
            continue
        kept.append(p)
    kept.sort(key=lambda x: x.rank)
    for i, p in enumerate(kept, start=1):
        p.rank = i
    out.proposals = kept


def _narrative(facts, att, session_gap, trailing_gap) -> List[str]:
    eq = facts.equity or {}
    lines = []
    lines.append(
        "{} trades, book {:+.2f}% (₹{:,.0f} -> ₹{:,.0f}); per-trade %-sum {:+.2f}%. "
        "Since inception the ₹1L book stands at {:+.2f}%.".format(
            facts.n_trades, eq.get("book_return_pct") or 0.0, eq.get("open_equity") or 0.0,
            eq.get("close_equity") or 0.0, facts.sum_net_pct(),
            eq.get("since_inception_pct") or 0.0))
    if facts.n_trades == 0:
        lines.append(
            "ZERO trades: the feed ran ({} bars) and {} setup(s) were evaluated, but nothing was "
            "taken. A flat day is a SELECTIVITY fact, not a performance result — it neither "
            "supports nor undermines the strategy, and it must not be read as progress toward "
            "+1.00%/day.".format(facts.log.bars_processed, facts.log.entry_evaluations))
    if session_gap is not None:
        lines.append("Gap to +1.00%/day (this session, n={}): {}".format(
            facts.n_trades, session_gap.verdict_line()))
    if trailing_gap is not None:
        lines.append("Gap to +1.00%/day ({} scope, the configuration actually running): {}".format(
            trailing_gap.scope, trailing_gap.verdict_line()))
    binding = facts.integrity.trade_count_binding
    lines.append(
        "Trade count was **{}**-bound ({} setups evaluated in the log, {} affordability skips, "
        "{} trades taken). This matters: a cash-bound or halt-bound day is not a measurement of "
        "the signal's selectivity.".format(
            binding, facts.log.entry_evaluations, facts.log.skips_affordability, facts.n_trades))
    if facts.micro.get("n_scored"):
        lines.append(
            "Microstructure shadow signal: {:.1f}% next-bar directional hit rate over {} scored "
            "bars vs the 50% coin flip. Shadow only — it never gates a trade.".format(
                (facts.micro.get("hit_rate") or 0.0) * 100.0, facts.micro["n_scored"]))
    if facts.movers.get("n_resolved"):
        lines.append(
            "Movers sleeve (separate research book): {} resolved predictions, ±5% hit rate "
            "{:.1f}% vs a {:.1f}% base rate; direction {} on {} calls; shadow legs sum "
            "{:+.2f}%.".format(
                facts.movers["n_resolved"], (facts.movers.get("hit_rate_big5") or 0.0) * 100.0,
                (facts.movers.get("base_rate_big5") or 0.0) * 100.0,
                "{:.0f}%".format((facts.movers.get("dir_accuracy") or 0.0) * 100.0)
                if facts.movers.get("dir_accuracy") is not None else "n/a",
                facts.movers.get("n_dir_called"),
                facts.movers.get("sleeve_cum_pnl_pct") or 0.0))
    return lines


def _hypotheses(facts, att, dead_ideas: List[str]) -> List[Hypothesis]:
    """Generate at most a handful of hypotheses, each phrased against a gradeable metric.

    Reads `dead_ideas` first and skips anything already refuted — that read is the mechanism by
    which the desk's intelligence accumulates instead of resetting every night.
    """
    out: List[Hypothesis] = []
    gov = att.governance or {}
    tca = att.tca or {}
    mean_pct = gov.get("mean_position_pct_of_book")

    def add(h: Hypothesis) -> None:
        if h.statement in dead_ideas:
            return
        out.append(h)

    if mean_pct and mean_pct > 30.0:
        add(Hypothesis(
            statement=("Position notional averaging ~{:.0f}% of the book makes daily P&L a "
                       "single-name lottery; capping notional per position would cut daily "
                       "variance without touching the entry signal.".format(mean_pct)),
            rationale=("risk_per_trade_pct={} divided by a P0-mandated stop of >=~0.57% forces "
                       "notional to ~{:.0f}% of equity per trade, so the book runs 1-2 effective "
                       "names and exhausts cash before the concurrency cap binds ({} "
                       "affordability skips today).".format(
                           facts.config.get("risk_per_trade_pct"), mean_pct,
                           facts.log.skips_affordability)),
            pre_registered_test=("Over the next >=10 sessions, mean position notional as a % of "
                                 "book falls below 30% under a gated cap, AND avgR does not "
                                 "deteriorate. Graded on metric `mean_pct_of_book`."),
            falsifiable_prediction=("If the cap is enabled, mean_pct_of_book < 30 within 10 "
                                    "sessions; if it stays >=30 the cap is not binding and the "
                                    "hypothesis is refuted."),
            metric="mean_pct_of_book", direction="lt", threshold=30.0,
            min_sessions=MIN_SESSIONS_FOR_LIVE_CLAIM, min_n=40))

    share = tca.get("cost_share_of_abs_gross")
    if share is not None:
        add(Hypothesis(
            statement=("With the P0 friction-in-R gate live, friction's share of |gross| settles "
                       "below the pre-registered 40% bar."),
            rationale=("This is the frozen P0 bar from STRATEGY_IMPROVEMENT_PLAN_2026-07 §P0(a). "
                       "Tonight's session share is {:.0f}%.".format(share * 100.0)),
            pre_registered_test=("cost_share_of_gross < 0.40 measured over >=10 gated sessions "
                                 "and >=40 trades (`scripts/preregistered_eval.py` owns the "
                                 "committed look; this ledger entry mirrors it)."),
            falsifiable_prediction="cost_share_of_gross < 0.40 at the 10-session mark.",
            metric="cost_share_of_gross", direction="lt", threshold=0.40,
            min_sessions=MIN_SESSIONS_FOR_LIVE_CLAIM, min_n=40))
        add(Hypothesis(
            statement=("Fixing friction is NOT sufficient: even with cost share inside the bar, "
                       "the gated book's avgR stays below the -0.05 floor, which would locate "
                       "the verdict on the SIGNAL rather than the plumbing."),
            rationale=("P0's pre-registered branch: '(a) passes and (c) fails => the signal "
                       "itself is dead'. Stated as a hypothesis so it gets graded rather than "
                       "argued."),
            pre_registered_test=("avg_r > -0.05 over >=10 gated sessions and >=40 trades. "
                                 "SUPPORTED means the signal survives; REFUTED means move to "
                                 "P4/data priorities."),
            falsifiable_prediction="avg_r > -0.05 at the 10-session mark.",
            metric="avg_r", direction="gt", threshold=-0.05,
            min_sessions=MIN_SESSIONS_FOR_LIVE_CLAIM, min_n=40))
    return out


def _proposals(facts, att, trailing_gap) -> List[Proposal]:
    """Config/code proposals. Every one ships DEFAULT OFF with a pre-registered evidence bar."""
    out: List[Proposal] = []
    gov = att.governance or {}
    mean_pct = gov.get("mean_position_pct_of_book") or 0.0
    max_pct = gov.get("max_position_pct_of_book") or 0.0

    if max_pct > 40.0:
        out.append(Proposal(
            target_file="config/risk.yaml",
            config_key="risk.max_position_pct_of_equity",
            current_value="(absent — no per-position notional cap exists)",
            proposed_value="0.0   # 0 == OFF (default). Try 25.0 after the evidence bar is met.",
            diff=(
                "--- a/config/risk.yaml\n"
                "+++ b/config/risk.yaml\n"
                "@@ risk:\n"
                "   max_concurrent_positions: {}\n"
                "+  # DESK PROPOSAL (default OFF). Cap one position's notional as a % of live\n"
                "+  # equity. Today the largest position was {:.0f}% of the book and the mean was\n"
                "+  # {:.0f}%, so max_concurrent_positions never binds — cash exhaustion picks the\n"
                "+  # trades instead of the signal. 0 == off; the human enables it, not the desk.\n"
                "+  max_position_pct_of_equity: 0.0\n".format(
                    facts.config.get("max_concurrent_positions"), max_pct, mean_pct)),
            rationale=("Sizing is derived from risk_per_trade_pct / stop_pct. With the P0 gate "
                       "forcing wide stops the quotient lands at ~{:.0f}% of equity, so the book "
                       "holds 1-2 names and the {} concurrency cap is decorative. This is an "
                       "arithmetic observation about the sizing formula, not a fitted "
                       "result.".format(mean_pct, facts.config.get("max_concurrent_positions"))),
            evidence_bar=("Ship gated, DEFAULT 0. Validate on the archive replay first (does a "
                          "25% cap change the retained trade set?), then >=10 shadow sessions "
                          "with mean_pct_of_book < 30 AND avgR no worse than the uncapped "
                          "cohort. Human applies; the desk never flips it."),
            expected_effect=("IN-SAMPLE ONLY: would have reduced today's largest position from "
                             "{:.0f}% to 25% of the book. Says nothing about P&L — smaller "
                             "positions shrink both tails.".format(max_pct)),
            effort="~0.5 day incl. tests", rank=1))

    br = (facts.equity or {}).get("book_return_pct")
    net = facts.sum_net_pct()
    if facts.halt and br is not None and abs(net - br) > 0.25:
        out.append(Proposal(
            target_file="signal_engine/engine/runner.py",
            config_key="risk.drawdown_uses_book_equity",
            current_value="(absent — _LossBreaker sums per-trade pnl_pct_net)",
            proposed_value="false   # default OFF; true switches the breaker to equity-weighted",
            diff=(
                "--- a/signal_engine/engine/runner.py\n"
                "+++ b/signal_engine/engine/runner.py\n"
                "@@ class _LossBreaker:\n"
                "-        pnl = float(pnl_pct_net or 0.0)\n"
                "-        self.realized_pnl_pct += pnl\n"
                "+        # DESK PROPOSAL (gated, default OFF): the breaker currently sums\n"
                "+        # per-trade percentages, which is size-blind. On {} the sum was\n"
                "+        # {:+.2f}% while the book moved {:+.2f}% — the halt fired on a number\n"
                "+        # {:.2f}pp away from the book's actual give-back. When\n"
                "+        # risk.drawdown_uses_book_equity is true, fold in ledger equity instead.\n"
                "+        pnl = float(pnl_pct_net or 0.0) if not self.use_book_equity \\\n"
                "+            else float(pnl_inr or 0.0) / max(1.0, opening_equity) * 100.0\n"
                "+        self.realized_pnl_pct += pnl\n".format(facts.day, net, br,
                                                                abs(net - br))),
            rationale=("The daily drawdown breaker halted the session at a summed per-trade "
                       "{:+.2f}% while the book's real give-back was {:+.2f}%. Positions ran at "
                       "{:.0f}-{:.0f}% of equity, so the two quantities cannot agree. This is a "
                       "measurement-correctness bug in a RISK GATE, independent of whether the "
                       "strategy has edge.".format(net, br, mean_pct, max_pct)),
            evidence_bar=("Not a P&L claim, so no P&L bar: correctness is demonstrated by (i) a "
                          "unit test showing the breaker's drawdown equals the ledger's "
                          "equity drawdown on a synthetic mixed-size session, and (ii) an "
                          "archive replay of {} reproducing the halt at the equity-weighted "
                          "threshold. Ships gated, DEFAULT OFF, because it changes live halting "
                          "behaviour.".format(facts.day)),
            expected_effect=("Would have moved today's halt: the equity-weighted drawdown was "
                             "{:.2f}% against a {:.2f}% cap, so the session would NOT have "
                             "halted at 13:25. That could be better or worse — untested.".format(
                                 abs(br), facts.config.get("daily_loss_pct") or 0.0)),
            effort="~0.5 day incl. tests + replay", rank=2))

    if trailing_gap is not None and not trailing_gap.closable_by_parameter_work:
        out.append(Proposal(
            target_file="docs/EDGE_ROADMAP.md",
            config_key="research.priority",
            current_value="survivorship-clean data build (standing #1)",
            proposed_value="unchanged — reaffirmed by tonight's gap arithmetic",
            diff=("(no code change proposed)\n"
                  "The trailing gap to +1.00%/day is {:.0f}pp of win rate. No parameter in "
                  "config/risk.yaml moves win rate by {:.0f}pp; the binding constraint is the "
                  "absence of a measured gross edge. The honest proposal is therefore to spend "
                  "effort on the STRUCTURAL items (survivorship-clean + delisting-inclusive "
                  "bhavcopy panel, delivery-%/volume features) rather than on tuning.".format(
                      trailing_gap.win_rate_gap_pp or 0.0, trailing_gap.win_rate_gap_pp or 0.0)),
            rationale=("Parameter work cannot bridge the measured gap; proposing a tuning change "
                       "here would be the desk manufacturing false progress."),
            evidence_bar=("Any new signal from that data must clear the standing bar: " +
                          EDGE_VERDICT_BAR),
            expected_effect="No live change. Redirects effort.",
            effort="data/infra project", rank=9))
    return out


def _ops_proposals(facts) -> List[Proposal]:
    """Instrumentation and ops proposals — safe on any session, including a dead one."""
    out: List[Proposal] = []
    if facts.log.skips_affordability or facts.log.entry_evaluations:
        out.append(Proposal(
            target_file="signal_engine/risk/manager.py",
            config_key="observability.persist_gate_rejections",
            current_value="(absent — gate rejections are never persisted)",
            proposed_value="true   # write-only instrumentation, no behaviour change",
            diff=("--- a/signal_engine/risk/manager.py\n"
                  "+++ b/signal_engine/risk/manager.py\n"
                  "@@ RiskManager.build_trade_plan\n"
                  "+        # DESK PROPOSAL: persist every rejection (symbol, ts, direction,\n"
                  "+        # entry, stop, target, the failing gate, and the gate's numbers) to a\n"
                  "+        # `plan_rejections` table. Today the desk CANNOT answer 'did the\n"
                  "+        # gates reject winners?' because rejected plans are discarded before\n"
                  "+        # they become rows — the most expensive class of bug is invisible.\n"),
            rationale=("A gate that rejects winners is the most expensive kind of bug, and it is "
                       "currently unmeasurable: {} setups were evaluated and {} affordability "
                       "skips were logged, but no rejected plan's geometry is stored, so the bar "
                       "archive cannot price what was thrown away.".format(
                           facts.log.entry_evaluations, facts.log.skips_affordability)),
            evidence_bar=("Pure instrumentation — no live behaviour changes, so the bar is a "
                          "passing test that a rejection is written and that the write cannot "
                          "raise into the entry path. Value is realised at the NEXT review, "
                          "which can then replay rejects."),
            expected_effect="No P&L effect. Makes one currently-unanswerable question answerable.",
            effort="~0.5 day incl. tests", rank=3))
    if not facts.integrity.usable:
        out.append(Proposal(
            target_file="signal_engine/scheduler.py",
            config_key="ops.alert_on_dead_session",
            current_value="(absent — a 0-bar session is silent)",
            proposed_value="true",
            diff=("--- a/signal_engine/scheduler.py\n"
                  "+++ b/signal_engine/scheduler.py\n"
                  "@@ live_job\n"
                  "+        # DESK PROPOSAL: after the session, if bars_processed == 0 (or the\n"
                  "+        # reconnect count is in the hundreds), send a Telegram warning. On\n"
                  "+        # {} the feed logged {} bars and {} reconnect attempts while every\n"
                  "+        # EOD job still ran and the dashboard still rendered — the outage was\n"
                  "+        # invisible until this review looked for it.\n".format(
                      facts.day, facts.log.bars_processed, facts.log.ws_reconnects)),
            rationale=("A dead session that looks alive is the worst ops failure mode: it "
                       "silently contaminates every downstream statistic and nobody is told."),
            evidence_bar=("Ops fix, not a strategy change: bar is a test that the alert fires at "
                          "0 bars and does not fire on a healthy session."),
            expected_effect="No P&L effect. Turns a silent outage into a push notification.",
            effort="~0.25 day", rank=4))
    return out


def _kill_criteria(facts, trailing_gap) -> List[str]:
    """State up front what result would make us abandon the strategy. Desks retire strategies."""
    out = [
        "KILL 1 (already pre-registered, P0 §evidence bar): at the 10-gated-session look, if "
        "cost share of |gross| is under 40% AND the book is still net-negative, the verdict is "
        "on the SIGNAL, not the plumbing — stop tuning `vwap_ema_adx` gates and exits.",
        "KILL 2: if trailing avgR's bootstrap 95% CI remains entirely below 0 at n>=318 trades "
        "(the powered-verdict count computed in the 8-session review), retire the strategy from "
        "the live paper book and keep it only as a cost/ops harness.",
        "KILL 3: if the desk's own proposals produce no measured improvement over 30 sessions, "
        "the nightly review is decision-support theatre — cut it to a weekly run.",
    ]
    if trailing_gap is not None and trailing_gap.impossible_at_any_win_rate:
        out.append(
            "KILL 4 (live now): the trailing gap is unreachable at ANY win rate given the "
            "realised payoff and trade count. Continuing to tune parameters toward +1%/day is "
            "not a plan; the only honest routes are a genuinely new information source or a "
            "different horizon.")
    return out


# --------------------------------------------------------------------------------------- #
# Optional LLM layer — one structured call, snapshot-grounded, fully fallback-safe
# --------------------------------------------------------------------------------------- #

def llm_enabled() -> bool:
    return os.getenv("SE_DESK_LLM", "0") == "1"


def verified_snapshot(facts, att, gaps: Dict) -> str:
    """Every number the prose may cite, computed in code. The ONLY source of truth for the LLM."""
    eq, gov, tca = facts.equity or {}, att.governance or {}, att.tca or {}
    sg, tg = gaps.get("session"), gaps.get("trailing")
    lines = [
        "VERIFIED SNAPSHOT (computed in code — the only source of truth):",
        "day={} integrity={} bars={} reconnects={}".format(
            facts.day, facts.integrity.verdict, facts.log.bars_processed,
            facts.log.ws_reconnects),
        "trades={} win_rate={} book_return_pct={} per_trade_sum_net_pct={}".format(
            facts.n_trades, round(att.book.get("win_rate") or 0.0, 4),
            round(eq.get("book_return_pct") or 0.0, 4), round(facts.sum_net_pct(), 4)),
        "gross_pct={} cost_pct={} alpha_pct={} tide_pct={} pnl_inr={} charges_inr={}".format(
            round(att.book.get("sum_gross_pct") or 0.0, 4),
            round(att.book.get("sum_cost_pct") or 0.0, 4),
            round(att.book.get("alpha_pct") or 0.0, 4),
            round(att.book.get("tide_pct") or 0.0, 4),
            round(att.book.get("sum_inr") or 0.0, 2),
            round(att.book.get("charges_inr") or 0.0, 2)),
        "cost_share_of_abs_gross={} mean_cost_in_r={} median_stop_pct={}".format(
            round(tca.get("cost_share_of_abs_gross") or 0.0, 4),
            round(tca.get("mean_cost_in_r") or 0.0, 4),
            round(tca.get("median_stop_pct") or 0.0, 4)),
        "max_position_pct_of_book={} mean_position_pct_of_book={} affordability_skips={}".format(
            round(gov.get("max_position_pct_of_book") or 0.0, 2),
            round(gov.get("mean_position_pct_of_book") or 0.0, 2),
            gov.get("affordability_skips")),
        "halted={} halt_message={!r}".format(gov.get("halted"),
                                             (gov.get("halt_message") or "")[:160]),
    ]
    for name, g in (("session", sg), ("trailing", tg)):
        if g is None:
            continue
        lines.append(
            "gap[{}]: n_per_day={} win_rate={} payoff={} required_win_rate={} "
            "win_rate_gap_pp={} impossible_at_any_win_rate={} closable_by_params={}".format(
                name, round(g.n_trades, 3), round(g.win_rate or 0.0, 4),
                round(g.payoff or 0.0, 4), round(g.required_win_rate or 0.0, 4),
                round(g.win_rate_gap_pp or 0.0, 2), g.impossible_at_any_win_rate,
                g.closable_by_parameter_work))
    lines.append("trailing_sessions={} trailing_trades={}".format(
        facts.trailing.get("sessions"), len(facts.trailing.get("trades") or [])))
    lines.append("INSTRUMENT IDENTITY: every symbol below is an NSE EQ series ticker as traded "
                 "by this engine's watchlist. NSE tickers are ambiguous (IDEA is Vodafone Idea "
                 "Ltd, not Ideanomics; several Jindal-family tickers differ by suffix). Do NOT "
                 "map a ticker to a company name, sector or news event; refer to it by ticker "
                 "only.")
    lines.append("SYMBOLS TRADED: " + ", ".join(sorted({str(t.get("symbol"))
                                                        for t in facts.trades})))
    return "\n".join(lines)


_SYSTEM = (
    "You are the most rigorous sceptic on a systematic trading desk, writing the commentary "
    "section of a nightly review of a PAPER intraday book on Indian equities.\n\n"
    "ABSOLUTE RULES:\n"
    "1. The VERIFIED SNAPSHOT is the only source of truth for every number. Never compute, "
    "estimate, or infer a figure that is not in it. If two inputs disagree, FLAG the "
    "discrepancy explicitly — never reconcile it into a new number.\n"
    "2. Cite n (trade count) or the session count for every claim. A claim without n is "
    "forbidden.\n"
    "3. Distinguish MECHANICAL findings (arithmetic identities, config facts — true at n=1) from "
    "STATISTICAL ones (claims about distributions, which need n and sessions). Label each.\n"
    "4. If the evidence is insufficient, write 'insufficient evidence' and stop. Do NOT invent a "
    "narrative to fill the gap. Absence of a finding is a valid output.\n"
    "5. Never claim an edge. Live-behaviour claims need >=10 sessions; research claims need the "
    "standing bar (" + EDGE_VERDICT_BAR + ").\n"
    "6. Never recommend applying a change. You write analysis; a human applies changes.\n"
    "7. No advice language, no forecasts, no 'tomorrow we will' — this is a paper book with no "
    "demonstrated edge.\n"
    "8. Refer to instruments by ticker only. Do not name companies, sectors, or news.\n"
)


def llm_commentary(facts, att, gaps: Dict, deterministic: AnalystOutput,
                   meter: Optional[CostMeter] = None) -> Dict:
    """One structured LLM call. Returns {} on ANY failure — the review never depends on it."""
    meter = meter or CostMeter()
    result: Dict = {"commentary": None, "model": None, "meter": meter, "error": None}
    if not llm_enabled():
        result["error"] = "SE_DESK_LLM is not 1 — deterministic path only (this is the default)."
        return result
    try:
        import anthropic
    except Exception as exc:  # noqa: BLE001
        result["error"] = "anthropic SDK unavailable: {}".format(exc)
        return result
    snapshot = verified_snapshot(facts, att, gaps)
    findings = "\n".join("- " + f.rendered() for f in att.findings)
    det = "\n".join("- " + line for line in deterministic.narrative)
    user = (
        snapshot + "\n\nDETERMINISTIC FINDINGS (already computed; do not restate mechanically, "
        "interrogate them):\n" + findings + "\n\nDETERMINISTIC NARRATIVE:\n" + det +
        "\n\nWrite three sections, plain prose, no invented numbers:\n"
        "(1) The one thing that actually mattered in this session, with n.\n"
        "(2) The strongest argument AGAINST treating any of tonight's findings as actionable.\n"
        "(3) What single piece of evidence, if gathered, would most change the picture.\n")
    model = DEEP_MODEL
    schema = {
        "type": "object",
        "properties": {
            "what_mattered": {"type": "string"},
            "strongest_counterargument": {"type": "string"},
            "highest_value_evidence": {"type": "string"},
            "discrepancies_flagged": {"type": "array", "items": {"type": "string"}},
            "sufficient_evidence": {"type": "boolean"},
        },
        "required": ["what_mattered", "strongest_counterargument", "highest_value_evidence",
                     "discrepancies_flagged", "sufficient_evidence"],
        "additionalProperties": False,
    }
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=4000, system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            result["error"] = "model declined the request (stop_reason=refusal)"
            return result
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        payload = json.loads(text)
        usage = getattr(resp, "usage", None)
        meter.record(model, getattr(usage, "input_tokens", 0) or 0,
                     getattr(usage, "output_tokens", 0) or 0)
        parts = ["**What mattered:** " + str(payload.get("what_mattered", "")),
                 "**Strongest counterargument:** "
                 + str(payload.get("strongest_counterargument", "")),
                 "**Highest-value next evidence:** "
                 + str(payload.get("highest_value_evidence", ""))]
        flagged = payload.get("discrepancies_flagged") or []
        if flagged:
            parts.append("**Discrepancies flagged (not reconciled):** "
                         + "; ".join(str(x) for x in flagged))
        if payload.get("sufficient_evidence") is False:
            parts.append("**The model reports the evidence is insufficient for a verdict.**")
        result["commentary"] = "\n\n".join(parts)
        result["model"] = model
    except Exception as exc:  # noqa: BLE001 - the nightly job must never die on an API error
        result["error"] = "LLM call failed, falling back to the deterministic report: {}".format(
            str(exc)[:200])
    return result
