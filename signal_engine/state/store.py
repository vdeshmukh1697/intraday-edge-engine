"""StateStore interface + in-memory implementation (PLAN §3.6).

At full-universe scale the plan uses Redis for O(1) latest-state reads decoupled from
compute. For the single-process MVP an in-memory dict is sufficient; a RedisStateStore
is a drop-in behind this interface when we go multi-process (deferred, see PROGRESS).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from signal_engine.domain.models import Bar


class StateStore(ABC):
    @abstractmethod
    def set_bar(self, symbol: str, bar: Bar) -> None: ...

    @abstractmethod
    def get_bar(self, symbol: str) -> Optional[Bar]: ...

    @abstractmethod
    def set_features(self, symbol: str, features: Dict[str, float]) -> None: ...

    @abstractmethod
    def get_features(self, symbol: str) -> Optional[Dict[str, float]]: ...

    @abstractmethod
    def set_leaderboard(self, entries: list) -> None: ...

    @abstractmethod
    def get_leaderboard(self) -> list: ...


class InMemoryStateStore(StateStore):
    def __init__(self):
        self._bars: Dict[str, Bar] = {}
        self._features: Dict[str, Dict[str, float]] = {}
        self._leaderboard: List = []

    def set_bar(self, symbol: str, bar: Bar) -> None:
        self._bars[symbol] = bar

    def get_bar(self, symbol: str) -> Optional[Bar]:
        return self._bars.get(symbol)

    def set_features(self, symbol: str, features: Dict[str, float]) -> None:
        self._features[symbol] = features

    def get_features(self, symbol: str) -> Optional[Dict[str, float]]:
        return self._features.get(symbol)

    def set_leaderboard(self, entries: list) -> None:
        self._leaderboard = list(entries)

    def get_leaderboard(self) -> list:
        return list(self._leaderboard)
