"""Walk-forward / time-split helpers (PLAN §6.4 — anti-overfitting).

Pure list/date logic for splitting an ordered sequence of trading days
into chronological train/validation/test segments and rolling
walk-forward windows. The defining property is "out-of-sample is
sacred": every test segment is strictly *after* its corresponding
train segment, so there is never any look-ahead leakage.

No I/O, no external dependencies. Python 3.9 compatible.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple, TypeVar

T = TypeVar("T")


def time_split(
    days: Sequence[T],
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> Tuple[List[T], List[T], List[T]]:
    """Split an ordered sequence into (train, val, test) chronologically.

    The input is sorted ascending first (callers need not pre-sort). The
    splits are non-overlapping and exhaustive: ``train + val + test``
    equals the sorted input. ``test`` is the remainder (the most recent
    items) so its fraction is implicitly ``1 - train_frac - val_frac``.

    Args:
        days: Ordered (or unordered) sequence of sortable items.
        train_frac: Fraction of items for training (>= 0).
        val_frac: Fraction of items for validation (>= 0).

    Returns:
        ``(train, val, test)`` as three lists.

    Raises:
        ValueError: If any fraction is negative, or if
            ``train_frac + val_frac >= 1.0`` (leaving no room for test).
    """
    if train_frac < 0 or val_frac < 0:
        raise ValueError("fractions must be non-negative")
    if train_frac + val_frac >= 1.0:
        raise ValueError(
            "train_frac + val_frac must be < 1.0 to leave room for the test set"
        )

    ordered = sorted(days)
    n = len(ordered)
    if n == 0:
        return [], [], []

    n_train = int(math.floor(n * train_frac))
    n_val = int(math.floor(n * val_frac))

    train = ordered[:n_train]
    val = ordered[n_train : n_train + n_val]
    test = ordered[n_train + n_val :]
    return train, val, test


def walk_forward_windows(
    days: Sequence[T],
    train_size: int,
    test_size: int,
    step: int = None,
) -> List[Tuple[List[T], List[T]]]:
    """Produce rolling walk-forward (train, test) windows.

    The input is sorted ascending first. Window ``k`` takes
    ``train = days[start : start + train_size]`` and
    ``test = days[start + train_size : start + train_size + test_size]``,
    then advances ``start`` by ``step``. Iteration stops as soon as a
    full ``test_size`` block can no longer be formed, so every emitted
    window has exactly ``train_size`` train items and ``test_size`` test
    items.

    Args:
        days: Ordered (or unordered) sequence of sortable items.
        train_size: Number of train items per window (>= 1).
        test_size: Number of test items per window (>= 1).
        step: Advance between windows. Defaults to ``test_size``
            (non-overlapping test segments).

    Returns:
        A list of ``(train_days, test_days)`` tuples. Empty if there is
        not enough data for even one full window.

    Raises:
        ValueError: If ``train_size < 1`` or ``test_size < 1``.
    """
    if train_size < 1:
        raise ValueError("train_size must be >= 1")
    if test_size < 1:
        raise ValueError("test_size must be >= 1")

    if step is None:
        step = test_size

    ordered = sorted(days)
    n = len(ordered)

    windows: List[Tuple[List[T], List[T]]] = []
    start = 0
    while start + train_size + test_size <= n:
        train = ordered[start : start + train_size]
        test = ordered[start + train_size : start + train_size + test_size]
        windows.append((train, test))
        start += step
    return windows
