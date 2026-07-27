"""Orchestration: facts -> forensics -> attribution -> gap -> ledger -> report + proposals.

Run order matters and is load-bearing:

1. `gather()` — facts, integrity first.
2. **Grade yesterday's hypotheses before generating tonight's.** Grading first means the new
   hypotheses are written in the light of what was just refuted, which is the whole "the agent's
   intelligence improves every day" mechanism.
3. If the session is not usable (dead feed, no session), **stop analysing**: emit an ops-only
   review that refuses to conclude. This is not a degraded mode, it is the correct output.
4. Otherwise: interrogate every trade, decompose P&L, solve the 1%/day gap, generate hypotheses
   and proposals, optionally add one LLM commentary block, write the report, persist, and send a
   short honest Telegram digest.

Outputs: `docs/desk/DESK_REVIEW_<day>.md`, rows in `desk_hypotheses` / `desk_proposals` /
`desk_gap` / `desk_reviews`, and the digest string. **No config file is ever written.**

    .venv/bin/python -m signal_engine.desk.review 2026-07-22
    .venv/bin/python -m signal_engine.desk.review 2026-07-22 --no-send
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from signal_engine.desk import analyst as _analyst
from signal_engine.desk.attribution import Attribution, decompose
from signal_engine.desk.facts import SessionFacts, gather, last_session_with_trades
from signal_engine.desk.forensics import interrogate, rejected_setups
from signal_engine.desk.gap import solve_from_facts, what_must_be_true
from signal_engine.desk.ledger import DeskStore, grade_open_hypotheses
from signal_engine.obs.logging_setup import get_logger

_log = get_logger("desk")

DEFAULT_OUT_DIR = "docs/desk"

HONESTY_BANNER = (
    "> **Paper book. No demonstrated edge. Proposals only — nothing in this review has been "
    "applied.**\n"
    "> The target (+1.00% net/day) is the user's stated goal, not a forecast. It is +250% a year "
    "even without compounding, and ~12x (+1,100%) with it — a level no institutional systematic "
    "desk sustains; the best systematic funds target ~1-3% per *month* gross. Every number below "
    "is measured; where the evidence is insufficient the review says so instead of narrating."
)


@dataclass
class DeskReview:
    day: str
    facts: SessionFacts
    attribution: Attribution
    gaps: Dict
    analyst: "_analyst.AnalystOutput"
    forensics: List = field(default_factory=list)
    grades: List = field(default_factory=list)
    rejects: Dict = field(default_factory=dict)
    markdown: str = ""
    report_path: Optional[str] = None
    digest: str = ""
    hypothesis_ids: List[str] = field(default_factory=list)
    proposal_ids: List[str] = field(default_factory=list)
    llm_error: Optional[str] = None

    @property
    def concluded(self) -> bool:
        """False when the desk refused to draw conclusions (dead / absent session)."""
        return self.facts.integrity.usable


def run_review(day: Optional[str] = None, cfg=None, db_url: Optional[str] = None,
               log_path: Optional[str] = "logs/launchd-scheduler.err.log",
               out_dir: str = DEFAULT_OUT_DIR, store: Optional[DeskStore] = None,
               write_report: bool = True, persist: bool = True,
               trading_day: Optional[bool] = None) -> DeskReview:
    """Run one nightly review. Deterministic; the LLM layer is opt-in via SE_DESK_LLM=1."""
    if cfg is None:
        try:
            from signal_engine.config import load_config

            cfg = load_config()
        except Exception:  # noqa: BLE001 - a config problem must not kill the review
            cfg = None
    if db_url is None:
        db_url = (cfg.env.db_url if cfg is not None
                  else "sqlite:///data/signal_engine.sqlite3")
    if day is None:
        day = last_session_with_trades(db_url) or ""

    facts = gather(day, db_url=db_url, log_path=log_path, cfg=cfg, trading_day=trading_day)
    own_store = store is None
    store = store or DeskStore(db_url)
    try:
        # 1) grade BEFORE generating — tonight's ideas must be informed by last night's verdicts.
        grades = grade_open_hypotheses(store, facts)

        forensics = interrogate(facts, cfg=cfg) if facts.integrity.usable else []
        att = decompose(facts, forensics)
        gaps = solve_from_facts(facts)
        out = _analyst.review_deterministic(facts, att, gaps, store.dead_ideas())
        rejects = rejected_setups(facts)

        llm_error = None
        if _analyst.llm_enabled():
            res = _analyst.llm_commentary(facts, att, gaps, out, meter=out.cost)
            out.llm_commentary = res.get("commentary")
            out.llm_model = res.get("model")
            llm_error = res.get("error")

        review = DeskReview(day=day, facts=facts, attribution=att, gaps=gaps, analyst=out,
                            forensics=forensics, grades=grades, rejects=rejects,
                            llm_error=llm_error)

        if persist:
            for h in out.hypotheses:
                review.hypothesis_ids.append(store.open_hypothesis(
                    day, h.statement, h.rationale, h.pre_registered_test,
                    h.falsifiable_prediction, h.metric, h.direction, h.threshold,
                    h.min_sessions, h.min_n))
            for p in out.proposals:
                review.proposal_ids.append(store.add_proposal(
                    day, p.target_file, p.config_key, p.current_value, p.proposed_value,
                    p.diff, p.rationale, p.evidence_bar, p.expected_effect, p.effort, p.rank))
            for g in gaps.values():
                store.save_gap(g.as_row())

        review.markdown = render_report(review, store)
        review.digest = build_digest(review)
        if write_report:
            review.report_path = _write(out_dir, day, review.markdown)
        if persist:
            eq = facts.equity or {}
            store.save_review({
                "day": day, "integrity_verdict": facts.integrity.verdict,
                "n_trades": facts.n_trades, "book_return_pct": eq.get("book_return_pct"),
                "sum_net_pct": facts.sum_net_pct(), "sum_inr": facts.sum_inr(),
                "gap_verdict": (gaps.get("gated") or gaps.get("trailing")).verdict_line()
                               if (gaps.get("gated") or gaps.get("trailing")) else None,
                "report_path": review.report_path,
                "n_findings": len(att.findings), "n_hypotheses": len(review.hypothesis_ids),
                "n_proposals": len(review.proposal_ids),
                "llm_used": int(bool(out.llm_commentary)),
                "llm_cost_inr": round(out.cost.total_inr, 4),
            })
        return review
    finally:
        if own_store:
            store.close()


def _write(out_dir: str, day: str, markdown: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "DESK_REVIEW_{}.md".format(day))
    with open(path, "w") as fh:
        fh.write(markdown)
    return path


# --------------------------------------------------------------------------------------- #
# Report rendering — institutional format
# --------------------------------------------------------------------------------------- #

def render_report(review: DeskReview, store: Optional[DeskStore] = None) -> str:
    f, att, gaps, out = review.facts, review.attribution, review.gaps, review.analyst
    eq = f.equity or {}
    L: List[str] = []
    L.append("# Desk review — {} ({})".format(f.day, f.config.get("strategy") or "n/a"))
    L.append("")
    L.append(HONESTY_BANNER)
    L.append("")

    # ---- 1. Executive summary
    L.append("## 1. Executive summary")
    L.append("")
    if not review.concluded:
        L.append("**No conclusions drawn.** Session integrity verdict: `{}`.".format(
            f.integrity.verdict))
        L.append("")
    for line in out.narrative:
        L.append("- " + line)
    L.append("")

    # ---- 2. Session integrity (always first after the summary)
    L.append("## 2. Session integrity")
    L.append("")
    L.append("| check | value |")
    L.append("|---|---|")
    L.append("| verdict | **{}** |".format(f.integrity.verdict))
    L.append("| usable for conclusions | **{}** |".format("YES" if f.integrity.usable else "NO"))
    L.append("| NSE trading day | {} |".format(f.integrity.trading_day))
    L.append("| live session started | {} |".format(f.log.live_started))
    L.append("| symbols subscribed | {} |".format(f.log.symbols_subscribed))
    L.append("| 1-min bars processed | {} |".format(f.log.bars_processed))
    L.append("| websocket reconnect attempts | {} |".format(f.log.ws_reconnects))
    L.append("| feed stream errors | {} |".format(f.log.feed_errors))
    L.append("| setups evaluated (ENTRY-CTX) | {} |".format(f.log.entry_evaluations))
    L.append("| affordability skips | {} |".format(f.log.skips_affordability))
    L.append("| trades taken | {} |".format(f.n_trades))
    L.append("| trade count bound by | **{}** |".format(f.integrity.trade_count_binding))
    L.append("| scheduler jobs missed | {} |".format(f.log.jobs_missed))
    L.append("| engine-log lines for the day | {} |".format(f.log.log_lines_for_day))
    L.append("")
    for r in f.integrity.reasons:
        L.append("- " + r)
    if f.log.notable:
        L.append("")
        L.append("Notable log lines:")
        L.append("")
        for n in f.log.notable[:8]:
            L.append("```\n{}\n```".format(n))
    L.append("")

    if not review.concluded:
        _render_dead_session_tail(L, review, store)
        return "\n".join(L)

    # ---- 3. P&L attribution
    L.append("## 3. P&L attribution")
    L.append("")
    b = att.book
    L.append("| component | value |")
    L.append("|---|---|")
    L.append("| gross (sum of per-trade %) | {:+.3f}% |".format(b.get("sum_gross_pct") or 0.0))
    L.append("| &nbsp;&nbsp;of which index tide | {:+.3f}% |".format(b.get("tide_pct") or 0.0))
    L.append("| &nbsp;&nbsp;of which alpha (picking) | {:+.3f}% |".format(
        b.get("alpha_pct") or 0.0))
    L.append("| friction (charges + slippage) | {:.3f}% |".format(b.get("sum_cost_pct") or 0.0))
    L.append("| net (sum of per-trade %) | {:+.3f}% |".format(b.get("sum_net_pct") or 0.0))
    L.append("| **book return (equity-weighted, the truth)** | **{:+.3f}%** |".format(
        b.get("book_return_pct") or 0.0))
    L.append("| realised ₹ | ₹{:+,.2f} |".format(b.get("sum_inr") or 0.0))
    L.append("| of which charges | ₹{:,.2f} |".format(b.get("charges_inr") or 0.0))
    L.append("| book equity | ₹{:,.2f} -> ₹{:,.2f} |".format(
        eq.get("open_equity") or 0.0, eq.get("close_equity") or 0.0))
    L.append("| identity residual (gross-cost-net) | {:+.6f}pp |".format(
        b.get("identity_residual_pp") or 0.0))
    L.append("")

    # ---- 4. TCA
    L.append("## 4. Transaction-cost analysis")
    L.append("")
    t = att.tca
    L.append("| metric | value |")
    L.append("|---|---|")
    L.append("| cost share of \\|gross\\| | {} |".format(
        "{:.1f}%".format((t.get("cost_share_of_abs_gross") or 0.0) * 100.0)
        if t.get("cost_share_of_abs_gross") is not None else "n/a"))
    L.append("| median stop width | {:.3f}% |".format(t.get("median_stop_pct") or 0.0))
    L.append("| mean friction in R (recorded charges only) | {:.3f}R |".format(
        t.get("mean_cost_in_r") or 0.0))
    L.append("| mean friction in R (all-in: charges + modelled slippage) | {} |".format(
        "{:.3f}R".format(t["mean_all_in_cost_in_r"])
        if t.get("mean_all_in_cost_in_r") is not None else "n/a"))
    L.append("| mean implementation shortfall | {:+.4f}% (n={}) |".format(
        t.get("mean_impl_shortfall_pct") or 0.0, t.get("n_shortfall") or 0))
    L.append("| modelled round-trip friction | {:.4f}% |".format(
        t.get("modeled_friction_pct") or 0.0))
    L.append("| modelled round-trip charges only | {:.4f}% |".format(
        t.get("modeled_charges_pct") or 0.0))
    L.append("| trades where friction decided the outcome | {} |".format(
        t.get("n_friction_decided") or 0))
    L.append("")

    # ---- 5. Risk governance
    L.append("## 5. Risk governance")
    L.append("")
    g = att.governance
    L.append("| metric | value |")
    L.append("|---|---|")
    L.append("| largest position (% of book) | {:.1f}% |".format(
        g.get("max_position_pct_of_book") or 0.0))
    L.append("| mean position (% of book) | {:.1f}% |".format(
        g.get("mean_position_pct_of_book") or 0.0))
    L.append("| effective concurrent names | {:.2f} |".format(
        g.get("effective_concurrent_names") or 0.0))
    L.append("| distinct symbols | {} |".format(g.get("n_symbols")))
    L.append("| best / worst name (₹) | {} |".format(
        "{} ₹{:+,.0f} / {} ₹{:+,.0f}".format(
            g["best_symbol"][0], g["best_symbol"][1], g["worst_symbol"][0], g["worst_symbol"][1])
        if g.get("best_symbol") and g.get("worst_symbol") else "n/a"))
    L.append("| same-minute entry bursts | {} |".format(len(g.get("burst_minutes") or {})))
    L.append("| intraday max drawdown (equity) | {:.2f}% |".format(
        g.get("intraday_maxdd_pct") or 0.0))
    L.append("| session halted | {} |".format(g.get("halted")))
    L.append("| configured daily drawdown cap | {:.2f}% |".format(
        g.get("daily_loss_pct_config") or 0.0))
    L.append("| configured max concurrent positions | {} |".format(
        g.get("max_concurrent_positions_config")))
    L.append("")
    if g.get("halt_message"):
        L.append("Halt message: `{}`".format(str(g["halt_message"])[:260]))
        L.append("")

    # ---- 6. Findings
    L.append("## 6. Findings (evidence class attached to every line)")
    L.append("")
    for fd in att.findings:
        L.append("- " + fd.rendered())
    L.append("")

    # ---- 7. Per-trade forensics
    L.append("## 7. Per-trade forensics")
    L.append("")
    L.append("| # | symbol | dir | entry | exit | reason | stop% | gross% | cost% | net% | R | "
             "MFE% | MAE% | captured | %book | ₹ |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(review.forensics, start=1):
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | "
                 "{} |".format(
                     i, x.symbol, x.direction, x.entry_ts[11:16], (x.exit_ts or "")[11:16],
                     x.exit_reason or "?", _u(x.stop_pct, 3), _n(x.pnl_pct_gross, 3),
                     _u(x.cost_pct, 4), _n(x.pnl_pct_net, 3), _n(x.r_multiple, 2),
                     _n(x.mfe_pct, 2), _n(x.mae_pct, 2), _captured(x),
                     _u(x.pct_of_book, 0), _n(x.pnl_inr, 0)))
    L.append("")
    L.append("### Counterfactual exits (in-sample; `optimal` is perfect-foresight)")
    L.append("")
    L.append("| # | symbol | recorded net% | baseline | trail | hold90 | wider stop | "
             "tighter stop | optimal |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(review.forensics, start=1):
        v = x.variants or {}
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            i, x.symbol, _n(x.pnl_pct_net, 3), _n(v.get("baseline"), 3), _n(v.get("trail"), 3),
            _n(v.get("hold90"), 3), _n(v.get("wider_stop"), 3), _n(v.get("tighter_stop"), 3),
            _n(v.get("optimal"), 3)))
    L.append("")
    L.append("Harness fidelity: `baseline` should reproduce `recorded net%` closely (same bars, "
             "same rules — residuals are live-tick-vs-bar timing). A large divergence means the "
             "replay is wrong, not that the strategy is different. **Every column here is "
             "in-sample and chosen from a fixed, unswept set; none is evidence.**")
    L.append("")
    L.append("**Standing prior that outranks this table:** the `trail` column will often look "
             "good on a handful of trades. It has already been tested properly — a 108-trade "
             "archive replay (STRATEGY_IMPROVEMENT_PLAN_2026-07 §P4) found trailing exits WORSE "
             "overall (-4.97% vs -3.85% baseline) and, on the P0-gated cohort specifically, "
             "baseline exits win (avgR +0.195 vs trail +0.142). A {}-trade in-sample table does "
             "not overturn that. Exits stay unchanged until the session-18 look.".format(
                 len(review.forensics)))
    L.append("")

    L.append("### The desk's standing questions, answered per trade")
    L.append("")
    for i, x in enumerate(review.forensics, start=1):
        L.append("**{}. {} {}** (`{}`)".format(i, x.symbol, x.direction, x.trade_id))
        L.append("")
        for line in x.question_answers():
            L.append("- " + line)
        for note in x.notes:
            L.append("- _note: {}_".format(note))
        L.append("")

    # ---- 8. What was rejected
    L.append("## 8. What the gates and the book rejected")
    L.append("")
    r = review.rejects
    L.append("- skip alerts: {} | advice alerts (setups surfaced): {} | setups evaluated in log: "
             "{} | affordability skips: {}".format(
                 r.get("n_skip_alerts"), r.get("n_advice_alerts"),
                 r.get("entry_evaluations_in_log"), r.get("affordability_skips_in_log")))
    if r.get("skips_by_symbol"):
        L.append("- skips by symbol: {}".format(r["skips_by_symbol"]))
    if r.get("surfaced_never_traded"):
        L.append("- surfaced but never traded: {}".format(", ".join(r["surfaced_never_traded"])))
    L.append("- **{}**".format(r.get("instrumentation_gap")))
    L.append("")

    # ---- 9. Buckets
    L.append("## 9. Bucket analysis")
    L.append("")
    L.append("Session buckets are structurally low-n (4-8 trades/day) and are shown for "
             "completeness only. Trailing buckets are where n can eventually clear the >=20 "
             "floor. Anything tagged NOT ACTIONABLE must not be acted on.")
    L.append("")
    for name in sorted(att.buckets):
        buckets = att.buckets[name]
        if not buckets:
            continue
        L.append("**{}**".format(name))
        L.append("")
        L.append("```")
        for bk in buckets[:12]:
            L.append(bk.rendered())
        L.append("```")
        L.append("")

    # ---- 10. The 1%/day gap model
    L.append("## 10. The +1.00%/day gap model")
    L.append("")
    for scope in ("session", "gated", "trailing"):
        gm = gaps.get(scope)
        if gm is None:
            continue
        L.append("### {} scope".format(scope.capitalize()))
        L.append("")
        L.append("| quantity | value |")
        L.append("|---|---|")
        L.append("| trades/day | {:.2f} |".format(gm.n_trades))
        L.append("| win rate | {} |".format(_pct(gm.win_rate)))
        L.append("| mean winner / mean loser magnitude (% of book) | {} / {} |".format(
            _u(gm.mean_winner_book_pct, 3), _u(gm.mean_loser_book_pct, 3)))
        L.append("| realised payoff | {} |".format(_u(gm.payoff, 2)))
        L.append("| realised daily book return | {} |".format(_n(gm.realised_daily_book_pct, 3)))
        L.append("| friction (% of book/day) | {} |".format(_u(gm.friction_book_pct, 3)))
        L.append("| friction as a share of the 1% target | {} |".format(
            _pct(gm.friction_share_of_target)))
        L.append("| **required win rate** | **{}** |".format(_pct(gm.required_win_rate)))
        L.append("| **win-rate gap** | **{} pp** |".format(_n(gm.win_rate_gap_pp, 1)))
        L.append("| required payoff at the current win rate | {} |".format(
            _u(gm.required_payoff, 2)))
        L.append("| required net edge per trade | {}% of book (₹{}) |".format(
            _u(gm.required_edge_per_trade_book_pct, 3),
            _u(gm.required_edge_per_trade_inr, 0)))
        L.append("| required net edge per trade in R | {} |".format(
            _n(gm.required_edge_per_trade_r, 2)))
        L.append("| required gross per day | {}% of book |".format(
            _u(gm.required_gross_book_pct, 3)))
        L.append("| closable by parameter work | **{}** |".format(
            "YES" if gm.closable_by_parameter_work else "NO"))
        L.append("")
        L.append("**{}**".format(gm.verdict_line()))
        L.append("")
        L.append("What would have to be true:")
        L.append("")
        for line in what_must_be_true(gm):
            L.append("- " + line)
        L.append("")
        for note in gm.notes:
            L.append("- _{}_".format(note))
        L.append("")

    if store is not None:
        series = store.fetch_gap_series("trailing", limit=30)
        if len(series) > 1:
            L.append("### Gap time series (trailing scope, newest first)")
            L.append("")
            L.append("| day | trades/day | win rate | required WR | gap pp | closable |")
            L.append("|---|---|---|---|---|---|")
            for row in series[:20]:
                L.append("| {} | {} | {} | {} | {} | {} |".format(
                    row["day"], _n(row.get("n_trades"), 2), _pct(row.get("win_rate")),
                    _pct(row.get("required_win_rate")), _n(row.get("win_rate_gap_pp"), 1),
                    "YES" if row.get("closable_by_parameter_work") else "NO"))
            L.append("")

    # ---- 11. Hypothesis ledger
    _render_ledger(L, review, store)

    # ---- 12. Proposals
    _render_proposals(L, review)

    # ---- 13. Kill criteria + priors + LLM
    L.append("## 13. Kill criteria (stated up front, as a desk would)")
    L.append("")
    for k in out.kill_criteria:
        L.append("- " + k)
    L.append("")
    _render_priors_and_llm(L, review, store)
    return "\n".join(L)


def _render_dead_session_tail(L: List[str], review: DeskReview,
                              store: Optional[DeskStore]) -> None:
    """For a dead/absent session: ops-only content, and an explicit refusal to analyse."""
    out = review.analyst
    L.append("## 3. Why no analysis follows")
    L.append("")
    L.append("A review that analyses a dead session manufactures findings out of an outage. The "
             "trade table, the attribution, the bucket analysis and the gap model are therefore "
             "**deliberately omitted** — not empty, omitted. Nothing about strategy quality, win "
             "rate, friction or progress toward +1.00%/day may be inferred from this day, in "
             "either direction.")
    L.append("")
    L.append("Trailing context is unchanged by today and is NOT restated here as progress; see "
             "the previous review with a usable session.")
    L.append("")
    L.append("## 4. Operational items")
    L.append("")
    for p in out.proposals:
        L.append("- **{}** (`{}`) — {}".format(p.target_file, p.config_key, p.rationale))
    if not out.proposals:
        L.append("- none")
    L.append("")
    _render_ledger(L, review, store, num=5)
    _render_proposals(L, review, num=6)
    L.append("## 7. Kill criteria")
    L.append("")
    for k in out.kill_criteria:
        L.append("- " + k)
    L.append("")
    _render_priors_and_llm(L, review, store, num=8)


def _render_ledger(L: List[str], review: DeskReview, store: Optional[DeskStore],
                   num: int = 11) -> None:
    L.append("## {}. Hypothesis ledger".format(num))
    L.append("")
    L.append("### Grading of previously-open hypotheses")
    L.append("")
    if not review.grades:
        L.append("- no open hypotheses to grade (this is the first review, or all are closed).")
    for gr in review.grades:
        L.append("- `{}` **{}** — {}".format(gr.hypothesis_id, gr.status.upper(), gr.statement))
        L.append("  - {}".format(gr.lesson))
    L.append("")
    L.append("### New hypotheses opened tonight (pre-registered tests, written before grading)")
    L.append("")
    if not review.analyst.hypotheses:
        L.append("- none. Either nothing new was gradeable, or every candidate was already in "
                 "the ledger — which is the point of reading it first.")
    for h, hid in zip(review.analyst.hypotheses,
                      review.hypothesis_ids or [""] * len(review.analyst.hypotheses)):
        L.append("- `{}` {}".format(hid or "(unpersisted)", h.statement))
        L.append("  - rationale: {}".format(h.rationale))
        L.append("  - pre-registered test: {}".format(h.pre_registered_test))
        L.append("  - falsifiable prediction: {}".format(h.falsifiable_prediction))
        L.append("  - graded on `{}` {} {} once >={} sessions and >={} trades exist".format(
            h.metric, ">" if h.direction == "gt" else "<", h.threshold,
            h.min_sessions, h.min_n))
    L.append("")


def _render_proposals(L: List[str], review: DeskReview, num: int = 12) -> None:
    out = review.analyst
    L.append("## {}. Ranked proposals — **NOT APPLIED**".format(num))
    L.append("")
    L.append("Every proposal below is a written diff for a human to apply. The desk has no code "
             "path that edits a config file or flips a flag, and `desk_proposals.applied` is "
             "hard-coded to 0 on insert.")
    L.append("")
    if not out.proposals:
        L.append("- No proposals tonight. An empty list is a valid output: the correct response "
                 "to insufficient evidence is to change nothing.")
    for p, pid in zip(out.proposals, review.proposal_ids or [""] * len(out.proposals)):
        L.append("### P{} — `{}` in `{}` {}".format(
            p.rank, p.config_key, p.target_file,
            "(`{}`)".format(pid) if pid else ""))
        L.append("")
        L.append("- current: `{}`".format(p.current_value))
        L.append("- proposed: `{}`".format(p.proposed_value))
        L.append("- rationale: {}".format(p.rationale))
        L.append("- **evidence bar (pre-registered):** {}".format(p.evidence_bar))
        L.append("- expected effect: {}".format(p.expected_effect))
        L.append("- effort: {}".format(p.effort))
        L.append("")
        L.append("```diff")
        L.append(p.diff.rstrip())
        L.append("```")
        L.append("")
    for d in out.dropped_proposals:
        L.append("- _{}_".format(d))
    if out.dropped_proposals:
        L.append("")


def _render_priors_and_llm(L: List[str], review: DeskReview,
                           store: Optional[DeskStore], num: int = 14) -> None:
    out = review.analyst
    L.append("## {}. Running priors — what we believe, and on what evidence".format(num))
    L.append("")
    priors = store.priors() if store is not None else []
    for p in priors[:24]:
        L.append("- " + p)
    L.append("")
    L.append("## {}. Reasoning layer".format(num + 1))
    L.append("")
    if out.llm_commentary:
        L.append("_LLM commentary ({}), grounded in the verified snapshot; the deterministic "
                 "sections above are unaffected by it._".format(out.llm_model))
        L.append("")
        L.append(out.llm_commentary)
        L.append("")
    else:
        L.append("Deterministic path only — no LLM call was made. {}".format(
            review.llm_error or "SE_DESK_LLM is not set to 1 (the default)."))
        L.append("")
    L.append(out.cost.summary())
    note = out.cost.unaffordable_note(
        (review.facts.equity or {}).get("open_equity", 0.0) / 100.0)
    if note:
        L.append("")
        L.append("- " + note)
    L.append("")
    L.append("---")
    L.append("")
    L.append("_Generated by `signal_engine/desk` (see docs/DESK_AGENT.md). Nightly analysis is "
             "one clock; evidence-gated change is the other. This document belongs entirely to "
             "the first._")
    return None


def _n(v, nd: int = 2) -> str:
    if v is None:
        return "n/a"
    try:
        return "{:+.{nd}f}".format(float(v), nd=nd)
    except (TypeError, ValueError):
        return "n/a"


def _captured(x) -> str:
    """How much of the favourable excursion the exit kept, phrased so it cannot mislead.

    `realised_gross / MFE` goes negative whenever a trade was up at some point and closed down,
    which reads as nonsense in a table. Report the plain fact instead: it gave the excursion back.
    """
    if x.mfe_pct is None:
        return "n/a"
    if x.mfe_pct <= 0:
        return "never favourable"
    if x.captured_frac is None or x.captured_frac <= 0:
        return "0% (was +{:.2f}% at best, gave it all back)".format(x.mfe_pct)
    return "{:.0f}%".format(x.captured_frac * 100.0)


def _u(v, nd: int = 2) -> str:
    """Unsigned formatter — for magnitudes and ratios, where a leading '+' reads as a claim."""
    if v is None:
        return "n/a"
    try:
        return "{:.{nd}f}".format(float(v), nd=nd)
    except (TypeError, ValueError):
        return "n/a"


def _pct(v) -> str:
    if v is None:
        return "n/a"
    try:
        return "{:.1f}%".format(float(v) * 100.0)
    except (TypeError, ValueError):
        return "n/a"


# --------------------------------------------------------------------------------------- #
# Telegram digest — short, honest, no "profit tomorrow" language
# --------------------------------------------------------------------------------------- #

def build_digest(review: DeskReview) -> str:
    f, out = review.facts, review.analyst
    eq = f.equity or {}
    if not review.concluded:
        return ("🛑 Desk review {} — NO CONCLUSIONS DRAWN.\nSession integrity: {} ({}).\n"
                "{} bars, {} reconnects, {} trades. A dead session cannot tell us anything "
                "about the strategy, so nothing was inferred.\nOps items: {}.".format(
                    f.day, f.integrity.verdict, f.integrity.reasons[0] if f.integrity.reasons
                    else "n/a", f.log.bars_processed, f.log.ws_reconnects, f.n_trades,
                    len(out.proposals)))
    gm = review.gaps.get("gated") or review.gaps.get("trailing")
    top = out.proposals[0] if out.proposals else None
    # "The one thing that mattered" = the highest-ranked ACTIONABLE MECHANICAL finding. Never a
    # statistical one: on 4-8 trades those are noise, and putting noise in a push notification
    # is how a review starts manufacturing conviction.
    ranked = sorted((x for x in review.attribution.findings
                     if x.actionable and x.kind in ("mechanical", "ops")),
                    key=lambda x: x.priority)
    mattered = " ".join((ranked[0].text if ranked else
                         (out.narrative[0] if out.narrative else "")).split())
    return ("🧮 Desk review {} ({} trades)\nBook {:+.2f}% (₹{:,.0f}); gross {:+.2f}% − friction "
            "{:.2f}% = net {:+.2f}% (per-trade sum).\nGap to +1%/day: {}\nThe one thing that "
            "mattered: {}\nTop proposal (NOT applied): {}\nPaper only. No edge implied.".format(
                f.day, f.n_trades, eq.get("book_return_pct") or 0.0,
                eq.get("close_equity") or 0.0,
                review.attribution.book.get("sum_gross_pct") or 0.0,
                review.attribution.book.get("sum_cost_pct") or 0.0,
                review.attribution.book.get("sum_net_pct") or 0.0,
                (gm.verdict_line() if gm else "not computable"),
                mattered[:220],
                "{} — {}".format(top.config_key, top.expected_effect[:110]) if top else "none"))


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - CLI
    import sys

    argv = list(argv if argv is not None else sys.argv[1:])
    send = "--no-send" not in argv
    argv = [a for a in argv if not a.startswith("--")]
    day = argv[0] if argv else None
    review = run_review(day=day)
    print(review.markdown)
    print("\n--- TELEGRAM DIGEST ---\n" + review.digest)
    if review.report_path:
        print("\nreport -> " + review.report_path)
    if send:
        try:
            from signal_engine.alerts import send_alert
            from signal_engine.config import load_config
            from signal_engine.factory import build_alerter

            cfg = load_config()
            send_alert(build_alerter(cfg), review.digest, level="info",
                       meta={"kind": "advice", "strategy": "desk_review"})
        except Exception as exc:  # noqa: BLE001
            print("(digest not sent: {})".format(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
