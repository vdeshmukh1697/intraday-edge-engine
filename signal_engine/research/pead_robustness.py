"""Tighten the jump-drift PEAD candidate before any paper sleeve (follow-up to ``pead_spread``).

The headline candidate (long earnings-up-jumpers / short earnings-down-jumpers, market-neutral via
futures, 20d) cleared every appropriate gate but not the literal monthly-PBO. Per the user's call we
TIGHTEN it three ways before committing anything:

  1. **F&O-restricted re-test.** The short-miss leg needs a single-stock FUTURE, so only F&O-eligible
     names are actually shortable. Re-run on a CONSERVATIVE, high-confidence NSE F&O subset (errs to
     EXCLUSION — it can only undercount n, never overstate implementability). Does the edge survive on
     definitely-shortable names?
  2. **Jump-magnitude × hold-period frontier.** Sweep |jump| thresholds × horizons and print the WHOLE
     surface (selecting the best cell is itself a mild overfit, so we don't cherry-pick — we look for a
     broad, monotone plateau vs a knife-edge).
  3. **Mid-cap / size-tier extension.** PEAD is larger where attention is scarcer. Split the universe by
     a liquidity proxy (median ``log_turnover_20d`` from the swing feature panel) into a large-cap half
     and a mid-cap half, and re-run. Is the drift stronger in the less-liquid (mid-cap) half?

Read-only, additive, reuses the cached parquets. Run:
  .venv/bin/python -m signal_engine.research.pead_robustness
"""

from __future__ import annotations

import pandas as pd

from signal_engine.research.delivery_costs import futures_short_leg_pct
from signal_engine.research.pead_spread import (
    build_event_panel, evaluate_spread, _pooled, _monthly_cluster_t,
)

# High-confidence NSE single-stock-FUTURES (F&O) members present in our 230-name universe.
# CONSERVATIVE by design: only names I am confident are F&O-eligible are listed, so the subset is a
# true lower bound on shortable coverage (excluding a real F&O name only costs sample; it never makes
# a non-shortable name look tradeable). Recent IPOs / uncertain additions are deliberately omitted.
FNO_CONFIDENT = frozenset({
    "ADANIPORTS", "AMBER", "AMBUJACEM", "ANGELONE", "ATGL", "BAJAJ-AUTO", "BAJAJFINSV", "BHARATFORG",
    "BRITANNIA", "CANBK", "CDSL", "CEATLTD", "CESC", "CHOLAFIN", "CUMMINSIND", "DALBHARAT", "DIXON",
    "DLF", "EICHERMOT", "ESCORTS", "FEDERALBNK", "FORTIS", "GMRAIRPORT", "GRANULES", "HDFCLIFE",
    "HINDALCO", "HINDPETRO", "ICICIPRULI", "IDEA", "IEX", "IGL", "INDIANB", "INOXWIND", "IRCTC",
    "JSL", "JSWENERGY", "KALYANKJIL", "LALPATHLAB", "LAURUSLABS", "LICI", "LT", "MANAPPURAM",
    "MANKIND", "MARICO", "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX", "MFSL", "MGL", "MOTHERSON",
    "MOTILALOFS", "MPHASIS", "MUTHOOTFIN", "NATCOPHARM", "NATIONALUM", "NAUKRI", "NAVINFLUOR", "NBCC",
    "NCC", "NESTLEIND", "NHPC", "NMDC", "NTPC", "NUVAMA", "OBEROIRLTY", "OFSS", "OIL", "ONGC",
    "PAGEIND", "PATANJALI", "PAYTM", "PERSISTENT", "PETRONET", "PFC", "PHOENIXLTD", "PIIND", "PNB",
    "POLICYBZR", "POLYCAB", "POONAWALLA", "POWERGRID", "PPLPHARMA", "PRESTIGE", "PVRINOX", "RAMCOCEM",
    "RBLBANK", "RECLTD", "RELIANCE", "SAIL", "SBICARD", "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN",
    "SIEMENS", "SJVN", "SOLARINDS", "SONACOMS", "SRF", "SUNPHARMA", "SUPREMEIND", "SUZLON", "SYNGENE",
    "TATACHEM", "TATACOMM", "TATACONSUM", "TATAELXSI", "TATAPOWER", "TATASTEEL", "TATATECH", "TCS",
    "TECHM", "TIINDIA", "TITAGARH", "TITAN", "TORNTPOWER", "TRENT", "TVSMOTOR", "UBL", "UNIONBANK",
    "UNITDSPR", "UPL", "VBL", "VEDL", "VOLTAS", "WIPRO", "YESBANK", "ZEEL", "ZYDUSLIFE",
})


def _jump_masks(panel: pd.DataFrame, thr: float = 0.0):
    """Long earnings-up-jumpers / short earnings-down-jumpers (|jump|>=thr), event rows only."""
    surp, jump = panel["surprise_at_entry"], panel["jump"]
    ev = surp.notna()
    return ev & (jump >= thr), ev & (jump <= -thr)


def _report(panel, beat, miss, leg, label, horizon=20):
    r1 = evaluate_spread(panel, beat, miss, horizon, leg_cost=leg, legs_per_position=1.0, label=label)
    r2 = evaluate_spread(panel, beat, miss, horizon, leg_cost=leg, legs_per_position=2.0, label=label)
    net, dates = _pooled(panel, beat, miss, horizon, leg)
    _, t, g = _monthly_cluster_t(net, dates)
    print(f"  {label:<30} n={r1.n:>4} WR={r1.win_rate*100:>4.1f}% PF={r1.profit_factor:>4.2f} "
          f"net/pos 1leg={r1.net_mean*100:>+5.2f}% 2leg={r2.net_mean*100:>+5.2f}% "
          f"t={t:>+4.2f}({g}mo) PBO={r1.pbo:.2f} {'✅' if r1.verdict.passed else '—'}")
    return r1


def fno_restricted(panel, leg):
    print("=== 1. F&O-restricted re-test (short leg needs single-stock futures) ===")
    names = set(panel["symbol"].unique())
    fno = names & FNO_CONFIDENT
    print(f"  Universe {len(names)} names → high-confidence F&O subset {len(fno)} "
          f"({len(names)-len(fno)} excluded as non-F&O / uncertain). Jump-sign, 20d, OOS.")
    in_fno = panel["symbol"].isin(fno)
    b_all, m_all = _jump_masks(panel)
    _report(panel, b_all, m_all, leg, "all 230 (baseline)")
    _report(panel, b_all & in_fno, m_all & in_fno, leg, "F&O-only (shortable)")
    # And the stronger |jump|>=3% variant on F&O only:
    b3, m3 = _jump_masks(panel, 0.03)
    _report(panel, b3 & in_fno, m3 & in_fno, leg, "F&O-only, |jump|>=3%")


def magnitude_hold_frontier(panel, leg):
    print("\n=== 2. Jump-magnitude × hold-period frontier (whole surface — no cherry-picking) ===")
    thrs = [0.0, 0.01, 0.02, 0.03, 0.05, 0.07]
    holds = [5, 10, 20]
    print(f"  net/pos (1-leg futures), OOS. cols=hold {holds}d ; rows=|jump|>= threshold")
    print(f"  {'|jump|':>7} | " + " | ".join(f"{h:>2}d (n / WR / PF / net%)" for h in holds))
    for thr in thrs:
        b, m = _jump_masks(panel, thr)
        cells = []
        for h in holds:
            r = evaluate_spread(panel, b, m, h, leg_cost=leg, compute_pbo=False, label="")
            cells.append(f"{r.n:>4}/{r.win_rate*100:>4.1f}/{r.profit_factor:>4.2f}/{r.net_mean*100:>+5.2f}")
        print(f"  >={thr*100:>4.1f}% | " + " | ".join(cells))
    print("  (Look for a broad monotone plateau — bigger jump → bigger drift — not a single hot cell.)")


def _symbol_liquidity() -> pd.Series:
    """Per-symbol median log_turnover_20d from the swing feature panel — a liquidity / size proxy."""
    sw = pd.read_parquet("data/research/swing_dataset.parquet", columns=["symbol", "log_turnover_20d"])
    return sw.groupby("symbol")["log_turnover_20d"].median()


def size_tier_extension(panel, leg):
    print("\n=== 3. Mid-cap / size-tier extension (liquidity proxy = median log_turnover_20d) ===")
    liq = _symbol_liquidity()
    names = sorted(set(panel["symbol"].unique()) & set(liq.index))
    if not names:
        print("  no liquidity overlap — skipped")
        return
    liq = liq.loc[names].sort_values()
    cut = liq.median()
    midcap = set(liq[liq <= cut].index)   # lower turnover ≈ smaller / mid-cap
    largecap = set(liq[liq > cut].index)  # higher turnover ≈ large-cap
    print(f"  {len(names)} names with liquidity data → mid-cap half {len(midcap)} / large-cap half "
          f"{len(largecap)} (split at median turnover). Jump-sign, 20d, OOS.")
    b, m = _jump_masks(panel)
    _report(panel, b & panel["symbol"].isin(largecap), m & panel["symbol"].isin(largecap), leg,
            "large-cap half (liquid)")
    _report(panel, b & panel["symbol"].isin(midcap), m & panel["symbol"].isin(midcap), leg,
            "mid-cap half (less liquid)")
    print("  Hypothesis: drift LARGER in the mid-cap half (scarcer attention → slower price discovery).")


def main() -> None:
    panel = build_event_panel()
    leg = futures_short_leg_pct()
    print(f"Jump-drift PEAD — robustness tightening. Futures leg cost {leg*100:.3f}%. "
          f"Gate: n>=2000, (WR>=52% OR PF>=1.10), PBO<0.10.\n")
    fno_restricted(panel, leg)
    magnitude_hold_frontier(panel, leg)
    size_tier_extension(panel, leg)
    print("\nReminder: candidate, not confirmed. Best frontier cells are mild overfit — judge the "
          "plateau + the F&O subset (true implementable n) + the size tilt, not any single max cell.")


if __name__ == "__main__":
    main()
