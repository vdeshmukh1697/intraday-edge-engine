"""The nightly quant-desk review ("the desk") — forensic analysis of each paper session.

**PROPOSALS ONLY. THE DESK NEVER APPLIES A CONFIG OR STRATEGY CHANGE.**

Read `docs/DESK_AGENT.md` before changing anything in this package. The design rests on two
clocks, and collapsing them is the single failure mode that would make this package harmful:

* **Nightly (fast, always runs):** gather facts, interrogate every trade, decompose P&L,
  compute the gap to the user's 1%/day target, grade yesterday's hypotheses, write proposals.
  Zero live parameter changes. Deterministic — works with no LLM and no network.
* **Gated (slow, evidence-bound):** any change to `config/risk.yaml` / `config/settings.yaml`
  or strategy params must be proposed with a pre-registered test + evidence bar, ship
  config-gated DEFAULT OFF, be validated on archive replay and/or >=10 sessions of shadow
  evidence, and be applied **only by the human**. The desk writes the diff; it never applies it.

Re-tuning live parameters nightly on 4-8 trades is a guaranteed overfitting machine, and this
repo already has three scalps to prove it (the universe allowlist killed three times on 3
names / 1 session; the "max 5 trades/day -> +6.9%" counterfactual that was pure arrival-order
artifact). Hence the low-n guards in `attribution.py` and the evidence bars in `ledger.py`.

Honesty contract (`docs/DESK_AGENT.md` §4): the review states n for every claim, separates
*mechanical/arithmetic* findings from *statistical* ones, refuses to conclude anything from a
dead session, and says plainly — with numbers — when the gap to 1%/day is not closable by
parameter work. An agent that reports fake progress toward 1%/day is worse than useless: it
would justify risking real money.

Deliberately NO re-export of `review` here: importing the orchestrator at package level would
make `python -m signal_engine.desk.review` emit a double-import RuntimeWarning, and it would pull
pandas into any process that only wanted the ledger (e.g. the read-only API endpoint).
Import what you need:

    from signal_engine.desk.review import run_review
    from signal_engine.desk.ledger import DeskStore
"""

from signal_engine.desk.facts import SessionFacts, gather
from signal_engine.desk.gap import GapModel, solve_gap
from signal_engine.desk.ledger import DeskStore

__all__ = ["SessionFacts", "gather", "GapModel", "solve_gap", "DeskStore"]
