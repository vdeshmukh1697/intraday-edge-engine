"""Tests for the walk-forward / time-split helpers (PLAN §6.4)."""

from datetime import date, timedelta

import pytest

from signal_engine.backtest.walkforward import time_split, walk_forward_windows


def _days(n):
    return [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------- time_split


def test_time_split_default_on_100():
    days = _days(100)
    train, val, test = time_split(days)
    assert len(train) == 60
    assert len(val) == 20
    assert len(test) == 20
    # Exhaustive and ordered: concatenation == sorted input.
    assert train + val + test == sorted(days)


def test_time_split_no_overlap_and_chronological():
    days = _days(100)
    train, val, test = time_split(days)
    assert max(train) < min(val)
    assert max(val) < min(test)
    # Disjoint sets.
    assert set(train).isdisjoint(val)
    assert set(val).isdisjoint(test)
    assert set(train).isdisjoint(test)


def test_time_split_10_items():
    days = _days(10)
    train, val, test = time_split(days, 0.6, 0.2)
    assert len(train) == 6
    assert len(val) == 2
    assert len(test) == 2


def test_time_split_sorts_unsorted_input():
    days = _days(10)
    shuffled = list(reversed(days))
    train, val, test = time_split(shuffled, 0.6, 0.2)
    assert train + val + test == days  # already ascending
    assert train == days[:6]
    assert val == days[6:8]
    assert test == days[8:]


def test_time_split_raises_when_no_room_for_test():
    with pytest.raises(ValueError):
        time_split(_days(10), 0.6, 0.4)  # sums to 1.0
    with pytest.raises(ValueError):
        time_split(_days(10), 0.8, 0.5)  # > 1.0


def test_time_split_raises_on_negative_frac():
    with pytest.raises(ValueError):
        time_split(_days(10), -0.1, 0.2)


def test_time_split_empty_input():
    assert time_split([]) == ([], [], [])


# ----------------------------------------------------- walk_forward_windows


def test_walk_forward_basic_shape_and_count():
    days = _days(100)
    windows = walk_forward_windows(days, train_size=30, test_size=10)
    # starts 0,10,20,30,40,50,60 -> 7 windows (70 would need 110 items).
    assert len(windows) == 7
    for train, test in windows:
        assert len(train) == 30
        assert len(test) == 10


def test_walk_forward_first_and_second_window_boundaries():
    days = _days(100)
    windows = walk_forward_windows(days, train_size=30, test_size=10)
    first_train, first_test = windows[0]
    assert first_train == days[0:30]
    assert first_test == days[30:40]
    # Default step == test_size == 10, so next test starts at index 40.
    _, second_test = windows[1]
    assert second_test == days[40:50]


def test_walk_forward_out_of_sample_is_sacred():
    days = _days(100)
    windows = walk_forward_windows(days, train_size=30, test_size=10)
    for train, test in windows:
        assert max(train) < min(test)


def test_walk_forward_sorts_unsorted_input():
    days = _days(100)
    windows = walk_forward_windows(list(reversed(days)), train_size=30, test_size=10)
    first_train, first_test = windows[0]
    assert first_train == days[0:30]
    assert first_test == days[30:40]


def test_walk_forward_invalid_sizes_raise():
    with pytest.raises(ValueError):
        walk_forward_windows(_days(100), train_size=0, test_size=10)
    with pytest.raises(ValueError):
        walk_forward_windows(_days(100), train_size=30, test_size=0)


def test_walk_forward_insufficient_data_returns_empty():
    assert walk_forward_windows(_days(20), train_size=30, test_size=10) == []
