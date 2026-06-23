"""Deterministic tests for the websocket shard manager (PLAN §3.3)."""

from __future__ import annotations

import pytest

from signal_engine.ingestion.shards import Shard, ShardManager


def _syms(n: int, prefix: str = "SYM") -> list:
    return [f"{prefix}{i}" for i in range(n)]


def test_partition_2500_into_three_shards():
    syms = _syms(2500)
    mgr = ShardManager(syms, max_per_shard=1000)
    assert mgr.num_shards == 3
    assert [len(s.symbols) for s in mgr.shards] == [1000, 1000, 500]

    # Union == original set, with no overlap across shards.
    seen = set()
    union = []
    for s in mgr.shards:
        assert not (seen & set(s.symbols))  # no overlap
        seen.update(s.symbols)
        union.extend(s.symbols)
    assert set(union) == set(syms)
    assert len(union) == len(syms)


def test_exactly_divisible():
    mgr = ShardManager(_syms(2000), max_per_shard=1000)
    assert mgr.num_shards == 2
    assert [len(s.symbols) for s in mgr.shards] == [1000, 1000]


def test_empty_universe():
    mgr = ShardManager([], max_per_shard=1000)
    assert mgr.num_shards == 0
    assert mgr.healthy_symbols() == []
    assert mgr.is_fully_healthy() is True
    assert mgr.assignment() == {}


def test_single_shard_when_below_max():
    mgr = ShardManager(_syms(250), max_per_shard=1000)
    assert mgr.num_shards == 1
    assert len(mgr.shards[0].symbols) == 250


def test_shard_for_returns_correct_id():
    syms = _syms(2500)
    mgr = ShardManager(syms, max_per_shard=1000)
    # A symbol in chunk 2 (indices 2000..2499) lives on shard 2.
    assert mgr.shard_for("SYM2200") == 2
    assert mgr.shard_for("SYM0") == 0
    assert mgr.shard_for("SYM1500") == 1


def test_shard_for_unknown_raises_keyerror():
    mgr = ShardManager(_syms(10), max_per_shard=1000)
    with pytest.raises(KeyError):
        mgr.shard_for("NOPE")


def test_health_lifecycle():
    syms = _syms(2500)
    mgr = ShardManager(syms, max_per_shard=1000)
    shard1_syms = set(mgr.shards[1].symbols)

    mgr.mark_down(1)
    healthy = set(mgr.healthy_symbols())
    assert not (healthy & shard1_syms)  # shard 1 symbols absent
    assert mgr.is_fully_healthy() is False
    assert mgr.down_shards() == [1]

    mgr.reconnect(1)
    assert shard1_syms <= set(mgr.healthy_symbols())  # restored
    assert mgr.is_fully_healthy() is True
    assert mgr.down_shards() == []


def test_healthy_symbols_order_preserved():
    syms = _syms(2500)
    mgr = ShardManager(syms, max_per_shard=1000)
    assert mgr.healthy_symbols() == syms


def test_mark_down_unknown_raises():
    mgr = ShardManager(_syms(10), max_per_shard=1000)
    with pytest.raises((KeyError, IndexError)):
        mgr.mark_down(5)
    with pytest.raises((KeyError, IndexError)):
        mgr.reconnect(5)


def test_assignment_covers_all_shards_and_symbols():
    syms = _syms(2500)
    mgr = ShardManager(syms, max_per_shard=1000)
    a = mgr.assignment()
    assert set(a.keys()) == {0, 1, 2}
    flat = [sym for sid in sorted(a) for sym in a[sid]]
    assert flat == syms


def test_rebalance_resizes_and_resets_health():
    mgr = ShardManager(_syms(2500), max_per_shard=1000)
    mgr.mark_down(0)
    assert mgr.is_fully_healthy() is False

    mgr.rebalance(_syms(1500, prefix="NEW"))
    assert mgr.num_shards == 2
    assert [len(s.symbols) for s in mgr.shards] == [1000, 500]
    assert mgr.is_fully_healthy() is True  # health reset
    assert mgr.shard_for("NEW1200") == 1
    with pytest.raises(KeyError):
        mgr.shard_for("SYM0")  # old universe gone


def test_shard_dataclass_defaults():
    s = Shard(id=0, symbols=["A", "B"])
    assert s.status == "up"
