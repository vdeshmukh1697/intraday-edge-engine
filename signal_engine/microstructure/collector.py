"""Live collector: buffers ticks per (symbol, minute) and emits one BarSignal per closed bar.

Wired into the live EngineRunner behind a flag. Every call is made from inside the runner's
tick / bar hooks, which already swallow exceptions — but the collector ALSO guards itself so
a microstructure failure can never perturb the trading path. Pure observability: it writes to
its own store and returns nothing the engine uses.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from signal_engine.microstructure.features import compute_bar_signal


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


class MicrostructureCollector:
    """Accumulate ticks by (symbol, minute); flush a signal when that bar closes."""

    def __init__(self, store) -> None:
        self.store = store
        self._buf: Dict[Tuple[str, datetime], List] = defaultdict(list)

    def on_tick(self, tick) -> None:
        try:
            self._buf[(tick.symbol, _minute(tick.ts))].append(tick)
        except Exception:  # noqa: BLE001 - never break the feed
            pass

    def on_bar_close(self, bar) -> None:
        """The bar for minute ``bar.ts`` just closed — compute + persist its signal."""
        try:
            key = (bar.symbol, _minute(bar.ts))
            ticks = self._buf.pop(key, None)
            if not ticks:
                return
            sig = compute_bar_signal(bar.symbol, key[1], ticks)
            if sig is not None and self.store is not None:
                self.store.save(sig)
            # Drop any stale buffers older than this bar for the symbol (defensive: a missed
            # roll shouldn't leak memory across a long session).
            stale = [k for k in self._buf if k[0] == bar.symbol and k[1] < key[1]]
            for k in stale:
                self._buf.pop(k, None)
        except Exception:  # noqa: BLE001 - shadow feature must never break the engine
            pass
