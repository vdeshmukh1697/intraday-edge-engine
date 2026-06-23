"""UniverseProvider contract — supplies the set of instruments to scan."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from signal_engine.universe.models import InstrumentMeta


class UniverseProvider(ABC):
    @abstractmethod
    def instruments(self) -> List[InstrumentMeta]:
        """Return all instruments in the universe."""

    def symbols(self) -> List[str]:
        return [i.symbol for i in self.instruments()]
