"""Market-neutral PEAD spread + SUE-with-jump — the survivorship-robust test (Option C).

The naive long-only PEAD result on this universe was a SURVIVORSHIP MIRAGE: the 230 *survivor*
names all drift up, so a long-beat leg looked like +1.75%/20d while its alpha vs the universe was
actually flat-to-negative (see ``docs/PEAD_FINDINGS.md``). What survives that correction is the
beat-vs-miss SPREAD — beats outperform misses — which only pays in a MARKET-NEUTRAL implementation
(long beats / short misses via futures).

This module formalizes that spread honestly:

* **Demeaning (survivorship + beta control).** Each post-earnings entry's forward return is demeaned
  by the SAME-DAY universe mean: ``r_hedged = fwd_ret_h - mean_over_names(fwd_ret_h | session)``.
  That subtracts the common survivor/market drift — exactly what makes the long-only number a
  mirage — and is also what a futures index-hedge realizes economically. The beat-minus-miss spread
  is, by construction, ``alpha_beat - alpha_miss`` (the universe drift cancels), so it is robust to
  the survivor up-drift.

* **Execution = single-stock FUTURES, both legs.** Cash shorts can't be held overnight in India.
  Each event-position is one single-stock futures leg (``futures_short_leg_pct`` ~13 bps round-trip);
  a beat/miss PAIR is two legs (~27 bps). We report the gate on the per-position pooled distribution
  (1 leg/position, the harness convention — the book nets the index hedge collectively) AND a
  conservative 2-leg/position sensitivity, so the truth is bracketed, not assumed.

* **Same honest gate as everything else.** Net of cost, OOS headline, monthly walk-forward PBO,
  ``edge_verdict`` (n>=2000, WR>=52% OR PF>=1.10, PBO<0.10). Plus a both-halves-of-OOS robustness
  check, because episodic event returns make a single PBO number noisy.

* **SUE-with-jump (Step 2).** The announcement reaction = ``close_{first session after} /
  close_{last session before} - 1``. The literature's sharper signal fires when the EPS surprise AND
  the price jump AGREE. We test whether confirmation sharpens the spread vs plain surprise.

Read-only w.r.t. production. Reuses the cached ``long_daily_panel.parquet`` + ``earnings_events``.

Run:  .venv/bin/python -m signal_engine.research.pead_spread
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from signal_engine.ml.evaluate import EdgeVerdict, edge_verdict, estimate_pbo
from signal_engine.research.delivery_costs import futures_short_leg_pct
from signal_engine.research.events_dataset import load_or_build as load_events
from signal_engine.research.long_panel import build_or_load as load_panel
from signal_engine.research.pead_experiment import attach_pead

_FWD = {5: "fwd_ret_5", 10: "fwd_ret_10", 20: "fwd_ret_20"}


# --------------------------------------------------------------------------- panel prep
def attach_universe_mean(panel: pd.DataFrame) -> pd.DataFrame:
    """Add ``univ_mean_{h}`` = cross-sectional mean forward return across all names per session.

    This is the market-neutral / survivorship hedge benchmark: demeaning a trade by this strips the
    common survivor + market drift (what an index futures hedge removes economically)."""
    panel = panel.copy()
    panel["session_date"] = pd.to_datetime(panel["session_date"])
    for h, col in _FWD.items():
        panel[f"univ_mean_{h}"] = panel.groupby("session_date")[col].transform("mean")
    return panel


def attach_jump(panel: pd.DataFrame) -> pd.DataFrame:
    """Add ``jump`` = announcement reaction at each entry row: entry close / previous session close
    - 1 (within symbol). At a PEAD entry (first session strictly after the announcement) the previous
    session is the last one on/before the announcement, so ``jump`` is the post-earnings gap move."""
    panel = panel.sort_values(["symbol", "session_date"]).reset_index(drop=True)
    prev_close = panel.groupby("symbol")["close"].shift(1)
    panel["jump"] = panel["close"] / prev_close - 1.0
    return panel


def attach_earnings_window(panel: pd.DataFrame, events: pd.DataFrame, days: int = 30) -> pd.DataFrame:
    """Flag rows that fall in the (ann_date, ann_date + ``days`` calendar] post-earnings window for
    their symbol — i.e. whose forward-return window is contaminated by a recent announcement. The
    clean non-event CONTROL is the complement: sessions with no announcement in the prior ``days``."""
    panel = panel.sort_values(["symbol", "session_date"]).reset_index(drop=True)
    ev = events.dropna(subset=["surprise_pct"])[["symbol", "ann_date"]].copy()
    ev["ann_date"] = pd.to_datetime(ev["ann_date"]).sort_values()
    # Per symbol: most-recent announcement on/before each session (vectorized asof via searchsorted).
    panel["_last_ann"] = pd.NaT
    for sym, g in panel.groupby("symbol"):
        evs = np.sort(ev.loc[ev["symbol"] == sym, "ann_date"].to_numpy())
        if len(evs) == 0:
            continue
        sess = g["session_date"].to_numpy()
        idx = np.searchsorted(evs, sess, side="right") - 1
        la = np.where(idx >= 0, evs[np.clip(idx, 0, len(evs) - 1)], np.datetime64("NaT"))
        panel.loc[g.index, "_last_ann"] = la
    delta = (panel["session_date"] - panel["_last_ann"]).dt.days
    panel["in_earnings_window"] = delta.between(0, days, inclusive="both")
    panel = panel.drop(columns=["_last_ann"])
    return panel


def build_event_panel() -> pd.DataFrame:
    """Cached 16y panel + surprise_at_entry + universe means + announcement jump."""
    panel = load_panel()  # cached long_daily_panel.parquet
    events = load_events()
    if panel.empty or events.empty:
        raise SystemExit("Missing cached panel or events — build long_panel / events_dataset first.")
    panel = attach_pead(panel, events)
    panel = attach_universe_mean(panel)
    panel = attach_jump(panel)
    panel = attach_earnings_window(panel, events)
    return panel


# --------------------------------------------------------------------------- evaluation
@dataclass
class SpreadResult:
    label: str
    horizon: int
    n: int                    # pooled positions (beats long + misses short)
    n_beat: int
    n_miss: int
    win_rate: float           # fraction of positions net-positive
    profit_factor: float      # pooled, net
    net_mean: float           # mean per-position net return (market-neutral)
    gross_spread: float       # alpha_beat - alpha_miss (demeaned, gross) per pair / 20d
    net_spread: float         # gross_spread - 2 * leg_cost (one pair, both legs)
    alpha_beat: float         # demeaned gross long-leg alpha
    alpha_miss: float         # demeaned gross short-leg-underlying alpha (raw, not flipped)
    pbo: float
    leg_cost: float
    verdict: EdgeVerdict

    def line(self) -> str:
        flag = "✅ PASS" if self.verdict.passed else "—"
        return (f"  {self.label:<34} n={self.n:>5} (B{self.n_beat}/M{self.n_miss}) "
                f"WR={self.win_rate*100:>4.1f}% PF={self.profit_factor:>4.2f} "
                f"netμ={self.net_mean*100:>+5.2f}% spread={self.gross_spread*100:>+5.2f}%→"
                f"{self.net_spread*100:>+5.2f}% PBO={self.pbo:.2f}  {flag}")


def _pooled_pbo(dates: np.ndarray, net: np.ndarray) -> float:
    """Monthly walk-forward PBO on the pooled net positions (consecutive months as IS/OOS pairs)."""
    if len(net) == 0:
        return 0.0
    s = pd.DataFrame({"m": pd.to_datetime(dates).to_period("M"), "net": net})
    pfs: List[float] = []
    for _, g in s.groupby("m"):
        r = g["net"].to_numpy()
        gains = r[r > 0].sum()
        losses = -r[r < 0].sum()
        pfs.append(float(gains / losses) if losses > 0 else (2.0 if gains > 0 else 0.0))
    if len(pfs) < 3:
        return 0.0
    return estimate_pbo(pfs[:-1], pfs[1:])


def evaluate_spread(
    panel: pd.DataFrame,
    beat_mask: pd.Series,
    miss_mask: pd.Series,
    horizon: int = 20,
    leg_cost: Optional[float] = None,
    legs_per_position: float = 1.0,
    oos_only: bool = True,
    label: str = "",
    compute_pbo: bool = True,
) -> SpreadResult:
    """Market-neutral PEAD spread: LONG demeaned beats, SHORT demeaned misses, via futures.

    Each event-position carries ``legs_per_position`` futures legs of cost (1 = book nets the index
    hedge collectively, the harness convention; 2 = each position individually index-hedged, the
    conservative bound). Returns are DEMEANED (``fwd - univ_mean``) so the survivor/market drift is
    stripped before P&L. Pooled per-position stats + edge_verdict; the demeaned gross/net SPREAD
    (one beat/miss pair) is reported alongside for comparability with PEAD_FINDINGS.
    """
    col, ucol = _FWD[horizon], f"univ_mean_{horizon}"
    if leg_cost is None:
        leg_cost = futures_short_leg_pct()
    cost = legs_per_position * leg_cost

    fwd = panel[col].to_numpy()
    univ = panel[ucol].to_numpy()
    r_hedged = fwd - univ  # market-neutral / survivorship-demeaned event return
    finite = np.isfinite(fwd) & np.isfinite(univ)
    purged = panel["is_purged"].to_numpy() if "is_purged" in panel else np.zeros(len(panel), bool)
    keep = finite & ~purged
    if oos_only:
        keep = keep & panel["is_oos"].to_numpy()

    beat = beat_mask.to_numpy() & keep
    miss = miss_mask.to_numpy() & keep

    # Long the beats (earn +r_hedged), short the misses (earn -r_hedged); each minus its leg cost.
    net_beat = r_hedged[beat] - cost
    net_miss = -r_hedged[miss] - cost
    net = np.concatenate([net_beat, net_miss])
    dates = np.concatenate([panel["session_date"].to_numpy()[beat],
                            panel["session_date"].to_numpy()[miss]])

    n = int(net.shape[0])
    n_beat, n_miss = int(beat.sum()), int(miss.sum())
    if n == 0:
        v = edge_verdict(0, 0.0, 0.0, 0.0)
        return SpreadResult(label, horizon, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, leg_cost, v)

    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else (np.inf if wins > 0 else 0.0)
    wr = float(np.mean(net > 0))
    alpha_beat = float(np.mean(r_hedged[beat])) if n_beat else 0.0
    alpha_miss = float(np.mean(r_hedged[miss])) if n_miss else 0.0
    gross_spread = alpha_beat - alpha_miss           # universe drift cancels → survivorship-robust
    net_spread = gross_spread - 2.0 * leg_cost        # one beat/miss pair = two futures legs

    pbo = _pooled_pbo(dates, net) if compute_pbo else 0.0
    v = edge_verdict(n, wr, pf if np.isfinite(pf) else 99.0, pbo)
    return SpreadResult(label, horizon, n, n_beat, n_miss, wr, pf, float(np.mean(net)),
                        gross_spread, net_spread, alpha_beat, alpha_miss, pbo, leg_cost, v)


# --------------------------------------------------------------------------- report
def _masks(panel: pd.DataFrame, thr: float) -> Tuple[pd.Series, pd.Series]:
    surp = panel["surprise_at_entry"]
    return surp >= thr, surp <= -thr


def _confirmed_masks(panel: pd.DataFrame, thr: float) -> Tuple[pd.Series, pd.Series]:
    """SUE-with-jump: surprise AND announcement jump must AGREE."""
    surp, jump = panel["surprise_at_entry"], panel["jump"]
    beat = (surp >= thr) & (jump > 0)
    miss = (surp <= -thr) & (jump < 0)
    return beat, miss


def _pooled(panel, beat_mask, miss_mask, horizon, leg_cost, legs=1.0, oos_only=True):
    """Return (net, dates) for the pooled market-neutral positions — long demeaned beats, short
    demeaned misses — for diagnostics (significance, by-year)."""
    col, ucol = _FWD[horizon], f"univ_mean_{horizon}"
    r = panel[col].to_numpy() - panel[ucol].to_numpy()
    purged = panel["is_purged"].to_numpy() if "is_purged" in panel else np.zeros(len(panel), bool)
    keep = np.isfinite(panel[col].to_numpy()) & np.isfinite(panel[ucol].to_numpy()) & ~purged
    if oos_only:
        keep = keep & panel["is_oos"].to_numpy()
    beat = beat_mask.to_numpy() & keep
    miss = miss_mask.to_numpy() & keep
    cost = legs * leg_cost
    net = np.concatenate([r[beat] - cost, -r[miss] - cost])
    dates = np.concatenate([panel["session_date"].to_numpy()[beat],
                            panel["session_date"].to_numpy()[miss]])
    return net, dates


def _monthly_cluster_t(net: np.ndarray, dates: np.ndarray) -> Tuple[float, float, int]:
    """Honest significance for an OVERLAPPING, episodic signal: collapse positions to one mean per
    calendar month, then t-test the monthly means vs 0. Treating each month (not each overlapping
    20d position) as the unit handles within-month return correlation conservatively.
    Returns (mean_per_position, t_stat, n_months)."""
    if len(net) == 0:
        return 0.0, 0.0, 0
    s = pd.DataFrame({"m": pd.to_datetime(dates).to_period("M"), "net": net})
    mm = s.groupby("m")["net"].mean().to_numpy()
    g = len(mm)
    if g < 2:
        return float(net.mean()), 0.0, g
    t = float(mm.mean() / (mm.std(ddof=1) / np.sqrt(g))) if mm.std(ddof=1) > 0 else 0.0
    return float(net.mean()), t, g


def _half_check(panel: pd.DataFrame, beat_mask, miss_mask, horizon, leg_cost) -> str:
    """Sub-period robustness: does the demeaned gross spread hold in BOTH halves of OOS?"""
    oos = panel[panel["is_oos"].to_numpy()]
    if oos.empty:
        return "no OOS"
    cut = oos["session_date"].median()
    out = []
    for name, sub_mask in (("H1", panel["session_date"] <= cut), ("H2", panel["session_date"] > cut)):
        r = evaluate_spread(panel, beat_mask & sub_mask, miss_mask & sub_mask, horizon=horizon,
                            leg_cost=leg_cost, label=name, compute_pbo=False)
        out.append(f"{name}: spread {r.gross_spread*100:+.2f}% (n={r.n}, net/pos {r.net_mean*100:+.2f}%)")
    return "  |  ".join(out)


def main() -> None:
    panel = build_event_panel()
    leg = futures_short_leg_pct()
    surp = panel["surprise_at_entry"]
    entries = surp.notna()
    oos_entries = entries & panel["is_oos"]
    print(f"Market-neutral PEAD spread — 16y panel, {panel['symbol'].nunique()} names")
    print(f"Post-earnings entries: {int(entries.sum())} ({int(oos_entries.sum())} OOS). "
          f"Futures leg cost {leg*100:.3f}% (pair = 2 legs = {2*leg*100:.3f}%).")
    print(f"Gate: n>=2000, (WR>=52% OR PF>=1.10), PBO<0.10. Returns are DEMEANED by the universe "
          f"(survivorship-robust). OOS headline.\n")

    print("=== Step 1: plain-surprise market-neutral spread (long beats / short misses, 20d) ===")
    print("  [1 futures leg/position — harness convention, book nets the index hedge]")
    step1 = {}
    for thr in (0.0, 5.0, 10.0, 25.0):
        b, m = _masks(panel, thr)
        r = evaluate_spread(panel, b, m, 20, leg_cost=leg, legs_per_position=1.0,
                            label=f"surprise |{thr:g}%|")
        step1[thr] = r
        print(r.line())

    print("\n  [conservative: 2 futures legs/position — each position individually index-hedged]")
    for thr in (0.0, 5.0):
        b, m = _masks(panel, thr)
        r = evaluate_spread(panel, b, m, 20, leg_cost=leg, legs_per_position=2.0,
                            label=f"surprise |{thr:g}%| (2-leg)")
        print(r.line())

    print("\n=== Spread across horizons (does the drift have legs?) — surprise |5%|, 1-leg ===")
    for h in (5, 10, 20):
        b, m = _masks(panel, 5.0)
        r = evaluate_spread(panel, b, m, h, leg_cost=leg, label=f"hold {h}d")
        print(r.line())

    print("\n=== Step 2: SUE-with-jump — surprise CONFIRMED by announcement jump (20d, 1-leg) ===")
    print("  Plain vs confirmed at each threshold (does requiring agreement sharpen the spread?):")
    for thr in (0.0, 5.0, 10.0):
        bp, mp = _masks(panel, thr)
        bc, mc = _confirmed_masks(panel, thr)
        rp = evaluate_spread(panel, bp, mp, 20, leg_cost=leg, label=f"plain |{thr:g}%|")
        rc = evaluate_spread(panel, bc, mc, 20, leg_cost=leg, label=f"confirmed |{thr:g}%|")
        print(rp.line())
        print(rc.line())

    print("\n=== Step 3: both-halves-of-OOS robustness (demeaned gross spread, surprise |5%|) ===")
    b, m = _masks(panel, 5.0)
    print("  plain    :", _half_check(panel, b, m, 20, leg))
    bc, mc = _confirmed_masks(panel, 5.0)
    print("  confirmed:", _half_check(panel, bc, mc, 20, leg))

    print("\n=== Step 2b: DECOMPOSE — is it the surprise, the jump, or the interaction? (20d, 1-leg) ===")
    jump = panel["jump"]
    decomp = {
        "surprise-only |5%|": _masks(panel, 5.0),
        "jump-only (sign, all events)": (entries & (jump > 0), entries & (jump < 0)),
        "confirmed (surprise|5%| AND jump)": _confirmed_masks(panel, 5.0),
        "CONTRADICTED (surprise|5%| vs jump)":  # beat-but-fell vs miss-but-rose (should be weak/reversed)
            ((surp >= 5.0) & (jump < 0), (surp <= -5.0) & (jump > 0)),
    }
    for lab, (b, m) in decomp.items():
        r = evaluate_spread(panel, b, m, 20, leg_cost=leg, label=lab)
        print(r.line())

    print("\n=== Step 2c: CONTROL — is the jump-drift earnings-specific, or generic momentum? ===")
    print("  Short-term moves normally REVERSE; post-earnings moves DRIFT. Compare same jump-sign→20d")
    print("  demeaned drift for EARNINGS-window jumps vs CLEAN non-earnings jumps (matched |jump|≥thr).")
    fwd20 = panel["fwd_ret_20"].to_numpy()
    u20 = panel["univ_mean_20"].to_numpy()
    dem = fwd20 - u20
    j = panel["jump"].to_numpy()
    oos = panel["is_oos"].to_numpy()
    purg = panel["is_purged"].to_numpy()
    inwin = panel["in_earnings_window"].to_numpy()
    isev = panel["surprise_at_entry"].notna().to_numpy()
    base = np.isfinite(dem) & np.isfinite(j) & oos & ~purg
    for thr in (0.02, 0.03, 0.05):
        def _drift(mask_up, mask_dn):
            up, dn = dem[mask_up], dem[mask_dn]
            spread = (up.mean() - dn.mean()) if len(up) and len(dn) else float("nan")
            return spread, len(up) + len(dn)
        ev_up = base & isev & (j >= thr); ev_dn = base & isev & (j <= -thr)
        ne_up = base & ~inwin & (j >= thr); ne_dn = base & ~inwin & (j <= -thr)
        ev_s, ev_n = _drift(ev_up, ev_dn)
        ne_s, ne_n = _drift(ne_up, ne_dn)
        verdict = "DRIFT≠momentum ✓" if (ev_s > 0 and ne_s <= ev_s * 0.5) else "ambiguous"
        print(f"  |jump|>={thr*100:.0f}%:  earnings-jump spread={ev_s*100:>+5.2f}% (n{ev_n})   "
              f"clean non-earnings jump spread={ne_s*100:>+5.2f}% (n{ne_n})   {verdict}")

    print("\n=== Step 3b: honest significance (monthly-clustered t on per-position net, OOS) ===")
    print("  Episodic + 20d-overlap → naive per-position t overstates; monthly-clustered is the fair test.")
    for lab, (b, m) in (("plain |5%|", _masks(panel, 5.0)),
                        ("jump-only", (entries & (jump > 0), entries & (jump < 0))),
                        ("confirmed |5%|", _confirmed_masks(panel, 5.0)),
                        ("confirmed |0%|", _confirmed_masks(panel, 0.0))):
        net, dates = _pooled(panel, b, m, 20, leg)
        mean, t, g = _monthly_cluster_t(net, dates)
        print(f"  {lab:<18} net/pos={mean*100:>+5.2f}%  monthly-clustered t={t:>+5.2f}  "
              f"({g} months, n={len(net)})")

    print("\n=== Step 3c: by-year demeaned gross spread (full sample, confirmed |5%|) — sign stability ===")
    bc, mc = _confirmed_masks(panel, 5.0)
    net_all, dates_all = _pooled(panel, bc, mc, 20, leg, oos_only=False)
    yr = pd.DataFrame({"y": pd.to_datetime(dates_all).year, "net": net_all})
    by = yr.groupby("y")["net"].agg(["mean", "size"])
    cells = [f"{int(y)}:{row['mean']*100:+.1f}%(n{int(row['size'])})" for y, row in by.iterrows()]
    pos_years = int((by["mean"] > 0).sum())
    print("  " + "  ".join(cells))
    print(f"  → positive in {pos_years}/{len(by)} years (net/pos, incl. cost)")

    # Sanity: the long-only (non-demeaned) survivorship mirage, to re-confirm why we go neutral.
    print("\n=== Sanity: long-only RAW vs DEMEANED (re-confirm the survivorship mirage) ===")
    for thr in (5.0,):
        b, _ = _masks(panel, thr)
        sel = b.to_numpy() & np.isfinite(panel["fwd_ret_20"].to_numpy()) & panel["is_oos"].to_numpy()
        raw = panel["fwd_ret_20"].to_numpy()[sel]
        dem = raw - panel["univ_mean_20"].to_numpy()[sel]
        print(f"  beat>=+{thr:g}%: RAW long-only μ={raw.mean()*100:+.2f}% (survivorship-inflated) "
              f"vs DEMEANED α={dem.mean()*100:+.2f}% (n={len(raw)})")

    print("\nReminder: PASS = candidate, not edge. Episodic event returns make PBO noisy — the "
          "both-halves check is the robustness backstop. Survivor universe; long-only is a mirage.")


if __name__ == "__main__":
    main()
