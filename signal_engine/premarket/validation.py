"""Pre-market open-validation (PLAN §4.8).

At the open, check whether the pre-market prediction panned out: did a
meaningful gap occur in the predicted direction, did the actual move match
the predicted bias, and did opening volume confirm it (vs a low-volume fade)?

Pure deterministic logic. Python 3.9 compatible.
"""

from __future__ import annotations

from signal_engine.premarket.models import GapBias, IndexOutlook, PreMarketPick, ValidationResult


def _sign(x: float) -> int:
    """Sign helper: sign(0) == 0, positive -> +1, negative -> -1."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _fmt_pct(x: float) -> str:
    """Format a percent with an explicit sign, e.g. +0.6% / -0.5%."""
    return "{:+.1f}%".format(x)


def validate_open(
    predicted_gap_pct: float,
    predicted_dir_sign: int,
    actual_gap_pct: float,
    actual_volume_ratio: float,
    gap_min: float = 0.2,
    vol_confirm: float = 1.2,
) -> ValidationResult:
    """Validate a pre-market prediction against the actual open.

    Args:
        predicted_gap_pct: expected open gap vs prior close (percent).
        predicted_dir_sign: expected direction (+1 / -1 / 0).
        actual_gap_pct: actual open gap vs prior close (percent).
        actual_volume_ratio: opening volume / typical (1.0 == normal).
        gap_min: minimum |gap| (percent) to count as a meaningful gap.
        vol_confirm: opening volume ratio at/above which volume confirms.
    """
    actual_sign = _sign(actual_gap_pct)
    predicted_gap_sign = _sign(predicted_gap_pct)

    gap_happened = (
        predicted_gap_sign != 0
        and actual_sign == predicted_gap_sign
        and abs(actual_gap_pct) >= gap_min
    )
    direction_correct = predicted_dir_sign != 0 and actual_sign == predicted_dir_sign
    volume_confirmed = actual_volume_ratio >= vol_confirm

    # Compose a short human-readable summary.
    pred_word = "gap-up" if predicted_gap_sign > 0 else ("gap-down" if predicted_gap_sign < 0 else "flat")
    head = "predicted {} {}; actual {}, vol {:.1f}x".format(
        _fmt_pct(predicted_gap_pct), pred_word, _fmt_pct(actual_gap_pct), actual_volume_ratio
    )

    if not direction_correct:
        outcome = "wrong direction"
    elif not gap_happened:
        outcome = "gap did not materialise"
    elif not volume_confirmed:
        outcome = "low-volume fade"
    else:
        outcome = "confirmed"

    note = "{} -> {}".format(head, outcome)

    return ValidationResult(
        gap_happened=gap_happened,
        direction_correct=direction_correct,
        volume_confirmed=volume_confirmed,
        note=note,
    )


_BIAS_SIGN = {GapBias.GAP_UP: 1, GapBias.GAP_DOWN: -1, GapBias.FLAT: 0}


def validate_index(
    outlook: IndexOutlook,
    actual_gap_pct: float,
    actual_volume_ratio: float = 1.0,
    **kw,
) -> ValidationResult:
    """Validate an :class:`IndexOutlook` against the actual index open."""
    dir_sign = _BIAS_SIGN[outlook.gap_bias]
    return validate_open(
        outlook.expected_gap_pct,
        dir_sign,
        actual_gap_pct,
        actual_volume_ratio,
        **kw,
    )


def validate_pick(
    pick: PreMarketPick,
    actual_gap_pct: float,
    actual_volume_ratio: float = 1.0,
    **kw,
) -> ValidationResult:
    """Validate a :class:`PreMarketPick` against the actual stock open."""
    return validate_open(
        pick.expected_gap_pct,
        pick.bias.sign,
        actual_gap_pct,
        actual_volume_ratio,
        **kw,
    )
