"""Long (~16y) daily price panel from Yahoo — for giving PEAD enough out-of-sample statistical
power. The Dhan archive is only 5y; earnings drift fires ~4x/name/year, so 5y leaves too few OOS
events (n=484) to validate. Yahoo daily history is free back to ~2010, which ~3x's the events.

Lean by design: only what PEAD needs — daily close + forward returns (5/10/20d) + the SAME global
calendar-date split + interval embargo used everywhere (``ml.train.date_split_indices``), so the
out-of-sample test is the recent ~30% of history and nothing leaks across the boundary.

Cache: ``data/research/long_daily_panel.parquet``. Run:
  .venv/bin/python -m signal_engine.research.long_panel
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from signal_engine.ml.train import date_split_indices
from signal_engine.research.events_dataset import load_or_build as load_events

warnings.filterwarnings("ignore")

CACHE = Path("data/research/long_daily_panel.parquet")
FWD_HORIZONS = (5, 10, 20)
MAX_LABEL_DAYS = 20
EMBARGO_CALENDAR_DAYS = 35
START = "2010-01-01"


def _event_symbols() -> List[str]:
    """Only fetch names we actually have earnings for (PEAD needs both)."""
    ev = load_events()
    return sorted(ev["symbol"].unique().tolist()) if not ev.empty else []


def fetch_daily(symbols: List[str], start: str = START, sleep_s: float = 0.25) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    ok = miss = 0
    for i, sym in enumerate(symbols):
        try:
            h = yf.Ticker(f"{sym}.NS").history(start=start, interval="1d", auto_adjust=True)
            if h is None or h.empty or "Close" not in h:
                miss += 1
            else:
                d = pd.DataFrame({
                    "symbol": sym,
                    "session_date": pd.to_datetime(h.index).tz_localize(None).normalize(),
                    "close": h["Close"].to_numpy(dtype=float),
                })
                frames.append(d)
                ok += 1
        except Exception:  # noqa: BLE001 - best-effort per symbol
            miss += 1
        if sleep_s:
            time.sleep(sleep_s)
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(symbols)} (ok={ok}, miss={miss})")
    print(f"fetched daily history for {ok}/{len(symbols)} names ({miss} missing)")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _add_forward_and_split(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol forward returns + the global date split with interval embargo (no look-ahead)."""
    panel = panel.sort_values(["symbol", "session_date"]).reset_index(drop=True)
    parts = []
    for _sym, g in panel.groupby("symbol"):
        g = g.copy()
        c = g["close"].to_numpy()
        for h in FWD_HORIZONS:
            fwd = np.full(len(c), np.nan)
            if len(c) > h:
                fwd[:-h] = c[h:] / c[:-h] - 1.0
            g[f"fwd_ret_{h}"] = fwd
        # ts_exit = entry + MAX_LABEL_DAYS trading days (capped at the last row), for the embargo.
        ts = g["session_date"].to_numpy().astype("datetime64[ns]")
        pos = np.arange(len(ts))
        exit_pos = np.minimum(pos + MAX_LABEL_DAYS, len(ts) - 1)
        g["ts"] = ts
        g["ts_exit"] = ts[exit_pos]
        parts.append(g)
    big = pd.concat(parts, ignore_index=True)
    train_idx, test_idx, _cut = date_split_indices(
        big["ts"].to_numpy(), big["ts_exit"].to_numpy(),
        test_frac=0.3, embargo_days=EMBARGO_CALENDAR_DAYS)
    is_oos = np.zeros(len(big), dtype=bool)
    is_oos[test_idx] = True
    in_split = np.zeros(len(big), dtype=bool)
    in_split[train_idx] = True
    in_split[test_idx] = True
    big["is_oos"] = is_oos
    big["is_purged"] = ~in_split  # neither train nor test = straddled the cutoff (embargoed)
    return big


def build_or_load(force: bool = False, limit: Optional[int] = None) -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_parquet(CACHE)
    syms = _event_symbols()
    if limit:
        syms = syms[:limit]
    print(f"Building long daily panel from Yahoo for {len(syms)} names since {START}...")
    raw = fetch_daily(syms)
    if raw.empty:
        return raw
    panel = _add_forward_and_split(raw)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(CACHE, index=False)
    print(f"cached -> {CACHE}  ({len(panel)} rows)")
    return panel


def main() -> None:
    p = build_or_load(force=True)
    if p.empty:
        print("No data.")
        return
    print(f"\nrows={len(p)} · names={p['symbol'].nunique()} · "
          f"{p['session_date'].min().date()}..{p['session_date'].max().date()} · "
          f"OOS={int(p['is_oos'].sum())} purged={int(p['is_purged'].sum())}")


if __name__ == "__main__":
    main()
