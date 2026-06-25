"""Earnings-events dataset for the PEAD (post-earnings-announcement-drift) research track.

Pulls per-symbol quarterly earnings ANNOUNCEMENT dates + analyst EPS estimate + reported EPS +
surprise% from Yahoo (``yfinance.Ticker.get_earnings_dates``) — free, no NSE scraping, history to
~2020 for established names. The surprise is consensus-based (estimate vs reported), a cleaner PEAD
input than a seasonal-random-walk proxy.

Point-in-time discipline lives downstream in the PEAD overlay: the tradeable entry is the first
session STRICTLY AFTER the announcement timestamp, so nothing uses the result before it is public.

Cache: ``data/research/earnings_events.parquet`` (re-fetch with ``force=True``). One-time fetch of
~250 names is rate-limited (~0.4s/name) so it takes a few minutes; cached thereafter.

Run:  .venv/bin/python -m signal_engine.research.events_dataset            # fetch+cache all archive names
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import List, Optional

import pandas as pd

warnings.filterwarnings("ignore")

CACHE = Path("data/research/earnings_events.parquet")


def _archive_symbols(limit: Optional[int] = None) -> List[str]:
    """The names in the swing dataset (so events line up with the price panel)."""
    df = pd.read_parquet("data/research/swing_dataset.parquet", columns=["symbol"])
    syms = sorted(df["symbol"].unique().tolist())
    return syms[:limit] if limit else syms


def fetch_earnings_events(symbols: List[str], limit_per_symbol: int = 28,
                          sleep_s: float = 0.4) -> pd.DataFrame:
    """Fetch announcement date + EPS estimate/reported + surprise% for each symbol from Yahoo.

    Returns a long DataFrame: symbol, ann_ts (tz-aware), ann_date (date), eps_est, eps_reported,
    surprise_pct. Best-effort: a symbol that errors or has no data is skipped (logged count)."""
    import yfinance as yf

    rows = []
    ok = miss = 0
    for i, sym in enumerate(symbols):
        try:
            t = yf.Ticker(f"{sym}.NS")
            ed = t.get_earnings_dates(limit=limit_per_symbol)
            if ed is None or len(ed) == 0:
                miss += 1
            else:
                ed = ed.reset_index()
                # Column names vary slightly across yfinance versions; normalise.
                date_col = next((c for c in ed.columns if "Earnings Date" in str(c)
                                 or str(c).lower() == "index"), ed.columns[0])
                for _, r in ed.iterrows():
                    ts = pd.Timestamp(r[date_col])
                    rep = r.get("Reported EPS")
                    if pd.isna(rep):
                        continue  # only realised (past) announcements are tradeable
                    rows.append({
                        "symbol": sym,
                        "ann_ts": ts,
                        "ann_date": ts.tz_localize(None).normalize() if ts.tzinfo else ts.normalize(),
                        "eps_est": float(r["EPS Estimate"]) if not pd.isna(r.get("EPS Estimate")) else None,
                        "eps_reported": float(rep),
                        "surprise_pct": float(r["Surprise(%)"]) if not pd.isna(r.get("Surprise(%)")) else None,
                    })
                ok += 1
        except Exception:  # noqa: BLE001 - best-effort per symbol
            miss += 1
        if sleep_s:
            time.sleep(sleep_s)
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(symbols)} fetched (ok={ok}, miss={miss}, events={len(rows)})")
    out = pd.DataFrame(rows)
    if not out.empty:
        out["ann_date"] = pd.to_datetime(out["ann_date"]).dt.tz_localize(None)
        out = out.sort_values(["symbol", "ann_date"]).reset_index(drop=True)
    print(f"fetched {len(out)} earnings events for {ok}/{len(symbols)} names ({miss} missing)")
    return out


def load_or_build(force: bool = False, limit: Optional[int] = None) -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_parquet(CACHE)
    syms = _archive_symbols(limit)
    print(f"Fetching earnings events for {len(syms)} archive names from Yahoo...")
    df = fetch_earnings_events(syms)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df.to_parquet(CACHE, index=False)
        print(f"cached -> {CACHE}")
    return df


def main() -> None:
    df = load_or_build(force=True)
    if df.empty:
        print("No events fetched.")
        return
    print(f"\nevents={len(df)} · names={df['symbol'].nunique()} · "
          f"dates {df['ann_date'].min().date()}..{df['ann_date'].max().date()}")
    have_surp = df["surprise_pct"].notna().sum()
    print(f"with surprise%: {have_surp} ({100 * have_surp / len(df):.0f}%)")
    print(df.groupby(df["ann_date"].dt.year).size().to_string())


if __name__ == "__main__":
    main()
