"""ML evaluation metrics — PLAN §4.7.

Pure numpy metric functions over arrays. Decoupled from the model: callers pass
already-computed probabilities and ground-truth labels. No imports from
ml/features.py or ml/model.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np


def _roc_auc(y_true: np.ndarray, probs: np.ndarray) -> float:
    """ROC AUC via the rank/pairs method.

    Over all (positive, negative) pairs, the fraction where prob_pos > prob_neg,
    counting ties (prob_pos == prob_neg) as 0.5. If only one class is present the
    AUC is undefined -> return 0.5 (neutral).
    """
    pos = probs[y_true == 1]
    neg = probs[y_true == 0]
    n_pos = pos.shape[0]
    n_neg = neg.shape[0]
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Compare every positive against every negative.
    diff = pos.reshape(-1, 1) - neg.reshape(1, -1)
    wins = np.sum(diff > 0.0)
    ties = np.sum(diff == 0.0)
    return float((wins + 0.5 * ties) / (n_pos * n_neg))


def evaluate(y_true: np.ndarray, probs: np.ndarray, threshold: float = 0.5) -> Dict[str, float]:
    """Compute classification metrics for binary labels and predicted probabilities.

    Returns a dict with keys: accuracy, auc, brier, base_rate, n.
    Empty input yields zeros / n=0 without crashing.
    """
    y_true = np.asarray(y_true, dtype=float)
    probs = np.asarray(probs, dtype=float)
    n = int(y_true.shape[0])

    if n == 0:
        return {"accuracy": 0.0, "auc": 0.0, "brier": 0.0, "base_rate": 0.0, "n": 0}

    preds = (probs >= threshold).astype(int)
    accuracy = float(np.mean(preds == y_true.astype(int)))
    auc = _roc_auc(y_true, probs)
    brier = float(np.mean((probs - y_true) ** 2))
    base_rate = float(np.mean(y_true))

    return {
        "accuracy": accuracy,
        "auc": auc,
        "brier": brier,
        "base_rate": base_rate,
        "n": n,
    }


def compare(
    y_true: np.ndarray, ml_probs: np.ndarray, baseline_probs: np.ndarray
) -> Dict[str, object]:
    """Compare ML probabilities against a baseline on the same labels.

    auc_gain  = ml.auc - baseline.auc        (positive == ML better)
    brier_gain = baseline.brier - ml.brier   (positive == ML better; lower brier is better)
    """
    m = evaluate(y_true, ml_probs)
    b = evaluate(y_true, baseline_probs)
    return {
        "ml": m,
        "baseline": b,
        "auc_gain": m["auc"] - b["auc"],
        "brier_gain": b["brier"] - m["brier"],
    }


# --- Edge gate (PLAN §3 / §5 — code-enforced go/no-go) ----------------------

# Hard thresholds from the plan's `edge_verdict()` gate. Keep them named so the
# verdict reasons are self-documenting and a test can pin each one.
EDGE_MIN_SAMPLES = 2000
EDGE_MIN_WIN_RATE = 0.52      # fraction (52%)
EDGE_MIN_PROFIT_FACTOR = 1.10
EDGE_MAX_PBO = 0.10


@dataclass(frozen=True)
class EdgeVerdict:
    """Result of the per-hypothesis edge gate. ``passed`` is the AND of all gates."""

    passed: bool
    reasons: List[str]
    n_samples: int
    win_rate: float
    profit_factor: float
    pbo: float


def edge_verdict(
    n_samples: int,
    win_rate: float,
    profit_factor: float,
    pbo: float,
    min_samples: int = EDGE_MIN_SAMPLES,
    min_win_rate: float = EDGE_MIN_WIN_RATE,
    min_profit_factor: float = EDGE_MIN_PROFIT_FACTOR,
    max_pbo: float = EDGE_MAX_PBO,
) -> EdgeVerdict:
    """Pass/fail edge gate (PLAN §3 / §5). An edge is "found" ONLY if ALL hold:

      1. ``n_samples >= min_samples``                              (default 2000)
      2. ``win_rate >= min_win_rate`` OR ``profit_factor >= min_profit_factor``
                                                                   (default 0.52 / 1.10)
      3. ``pbo < max_pbo``                                         (default 0.10)

    ``win_rate`` is a FRACTION in [0, 1] (0.52 == 52%). The recent-6-month-holdout check
    (gate 4 in the plan) is a data-availability concern owned by the caller, not encodable
    from these scalars, so it is intentionally out of scope here. Returns an
    :class:`EdgeVerdict` carrying the boolean plus a human-readable reason per gate.
    """
    reasons: List[str] = []

    g_n = n_samples >= min_samples
    reasons.append(
        f"n={n_samples} {'>=' if g_n else '<'} {min_samples} "
        f"({'ok' if g_n else 'FAIL: too few samples'})")

    g_perf = (win_rate >= min_win_rate) or (profit_factor >= min_profit_factor)
    reasons.append(
        f"win_rate={win_rate:.3f} (>= {min_win_rate}?) OR pf={profit_factor:.3f} "
        f"(>= {min_profit_factor}?) ({'ok' if g_perf else 'FAIL: no performance edge'})")

    g_pbo = pbo < max_pbo
    reasons.append(
        f"pbo={pbo:.3f} {'<' if g_pbo else '>='} {max_pbo} "
        f"({'ok' if g_pbo else 'FAIL: likely overfit'})")

    passed = bool(g_n and g_perf and g_pbo)
    return EdgeVerdict(passed=passed, reasons=reasons, n_samples=int(n_samples),
                       win_rate=float(win_rate), profit_factor=float(profit_factor),
                       pbo=float(pbo))


def estimate_pbo(is_pfs: Sequence[float], oos_pfs: Sequence[float]) -> float:
    """Simple Probability of Backtest Overfitting estimate (Bailey et al., simplified).

    Given paired in-sample (IS) and out-of-sample (OOS) profit factors across N walk-forward
    windows / configurations, PBO is the fraction of windows in which the configuration that
    looked BEST in-sample under-performed the OOS MEDIAN — i.e. the IS-best choice landed in
    the bottom half OOS. A genuine edge ranks consistently (low PBO); an overfit one ranks
    well IS but randomly OOS (PBO -> 0.5).

    This lightweight variant ranks per-window: for each window, if that window's IS PF is at
    or above the IS median (an "IS-good" window) but its OOS PF is below the OOS median, it
    counts as an overfit instance. Returns the fraction of IS-good windows that decayed OOS,
    in [0, 1]. Fewer than 2 windows -> 0.0 (not estimable -> treat as no evidence of overfit).
    """
    is_arr = np.asarray(is_pfs, dtype=float)
    oos_arr = np.asarray(oos_pfs, dtype=float)
    n = is_arr.shape[0]
    if n < 2 or oos_arr.shape[0] != n:
        return 0.0
    is_med = float(np.median(is_arr))
    oos_med = float(np.median(oos_arr))
    is_good = is_arr >= is_med
    n_good = int(np.sum(is_good))
    if n_good == 0:
        return 0.0
    decayed = int(np.sum(is_good & (oos_arr < oos_med)))
    return float(decayed / n_good)
