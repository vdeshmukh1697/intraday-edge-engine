"""Websocket shard manager (interface + mock driver) (PLAN §3.3).

A single broker websocket connection cannot hold the full ~2,000-instrument
universe, so the universe is partitioned across N shards (connections). Each
shard tracks a health ``status`` ("up"/"down") so the engine can reason about
which symbols currently have live data and which need a reconnect.

This module is PURE LOGIC: it models the partitioning and the health lifecycle
so it is fully unit-testable offline. No real network / broker calls happen here
-- real Dhan wiring is deferred and gated elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Shard:
    """A single websocket connection holding a slice of the universe."""

    id: int
    symbols: List[str] = field(default_factory=list)
    status: str = "up"  # "up" or "down"


class ShardManager:
    """Partition a symbol universe across health-tracked websocket shards."""

    def __init__(self, symbols: List[str], max_per_shard: int = 1000):
        if max_per_shard < 1:
            raise ValueError("max_per_shard must be >= 1")
        self.max_per_shard = max_per_shard
        self.shards: List[Shard] = []
        self._index: Dict[str, int] = {}
        self._partition(list(symbols))

    # --- construction -----------------------------------------------------
    def _partition(self, symbols: List[str]) -> None:
        """(Re)build shards + symbol->shard index from ``symbols`` in order."""
        self.shards = []
        self._index = {}
        m = self.max_per_shard
        for shard_id, start in enumerate(range(0, len(symbols), m)):
            chunk = symbols[start:start + m]
            self.shards.append(Shard(id=shard_id, symbols=chunk))
            for sym in chunk:
                self._index[sym] = shard_id

    # --- topology ---------------------------------------------------------
    @property
    def num_shards(self) -> int:
        return len(self.shards)

    def assignment(self) -> Dict[int, List[str]]:
        """Map each shard id -> its list of symbols."""
        return {s.id: list(s.symbols) for s in self.shards}

    def shard_for(self, symbol: str) -> int:
        """Return the id of the shard holding ``symbol`` (O(1))."""
        return self._index[symbol]

    # --- health lifecycle -------------------------------------------------
    def _get(self, shard_id: int) -> Shard:
        if shard_id < 0 or shard_id >= len(self.shards):
            raise KeyError(shard_id)
        return self.shards[shard_id]

    def mark_down(self, shard_id: int) -> None:
        self._get(shard_id).status = "down"

    def reconnect(self, shard_id: int) -> None:
        self._get(shard_id).status = "up"

    def healthy_symbols(self) -> List[str]:
        """Symbols on shards whose status == 'up' (order preserved)."""
        out: List[str] = []
        for s in self.shards:
            if s.status == "up":
                out.extend(s.symbols)
        return out

    def down_shards(self) -> List[int]:
        return [s.id for s in self.shards if s.status == "down"]

    def is_fully_healthy(self) -> bool:
        return all(s.status == "up" for s in self.shards)

    # --- universe changes -------------------------------------------------
    def rebalance(self, new_symbols: List[str]) -> None:
        """Re-partition for an updated universe; all new shards start 'up'."""
        self._partition(list(new_symbols))
