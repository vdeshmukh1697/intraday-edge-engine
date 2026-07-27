"""Movers sleeve — the daily job: resolve yesterday's predictions, predict tomorrow, alert.

A PRE-REGISTERED prediction experiment with a paper shadow book — NOT a promised profit
machine. The sleeve exists to MEASURE whether the calibrated bucket probabilities hold
live (precision@k vs base rate, direction accuracy, and a ₹1L shadow P&L on the fillable,
direction-called subset). It never touches the main ₹1L intraday book.

Shadow-book rules (fixed, written down before any data came in):
* Universe: that day's top predictions, ranked FILLABILITY-FIRST — the tradeable (ADV>=₹5cr)
  names take ranks 1..k, then un-fillable circuit-lockers as a research-only tail.
* Tradeable subset: ``fillable`` AND ``pred_dir == 'LONG'`` (SHORT is hypothetical in the
  Indian cash market and is tracked as prediction-only).
* Entry next-day open, exit same-day close, equal-weight ₹20,000 notional per name,
  minus the intraday cost model's round-trip friction. Un-fillable names resolve their
  prediction (hit/miss) but book NO P&L.
* "Hit" = |close-to-close move| >= 5% on the predicted day.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from signal_engine.movers import predictor

SLEEVE_NOTIONAL = 20000.0  # ₹ per position (₹1L / top-5 tradeable)


def next_trading_day(cal, d: date) -> date:
    nxt = d + timedelta(days=1)
    for _ in range(10):
        if cal.is_trading_day(nxt):
            return nxt
        nxt += timedelta(days=1)
    return nxt


def _yahoo_daily_open_close(symbol: str, day: date):
    """FALLBACK price source for a single session: Yahoo daily OHLC.

    The nightly archive covers ~2,050 NSE names but MISSES thin ones (AARTECH, MAZDA, PPAP,
    DBSTOCKBRO, KAUSHALYA...). Those are exactly the circuit-lockers this sleeve predicts, so
    without a fallback their predictions stayed PERMANENTLY unscored — silently biasing the
    scoreboard toward only the names the archive happens to carry. Returns (open, close) or None.
    """
    from datetime import timedelta as _td

    import yfinance as yf

    df = yf.download(f"{symbol}.NS", start=day.isoformat(),
                     end=(day + _td(days=1)).isoformat(), interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return None
    o, c = df["Open"], df["Close"]
    if hasattr(o, "columns"):      # yfinance MultiIndex for a single ticker
        o, c = o.iloc[:, 0], c.iloc[:, 0]
    return float(o.iloc[0]), float(c.iloc[0])


def _open_close(store, symbol: str, day: date, allow_fallback: bool = True):
    """Session open/close from the archive, falling back to Yahoo for un-archived names."""
    try:
        df = store.load_session(symbol, day)
    except Exception:  # noqa: BLE001
        df = None
    if df is not None and len(df):
        return float(df["open"].iloc[0]), float(df["close"].iloc[-1])
    if allow_fallback:
        try:
            return _yahoo_daily_open_close(symbol, day)
        except Exception:  # noqa: BLE001 - network/ticker miss: stays pending, retried later
            return None
    return None


def resolve_pending(repo, store, cost_pct: float, today: date,
                    lookback_days: int = 30) -> Dict:
    """SELF-HEALING sweep: resolve every unresolved prediction for days <= ``today``.

    resolve_predictions alone only ever touched its own day, so anything that could not be
    priced that evening (thin name missing from the archive, a failed job, a machine asleep)
    was stuck unscored forever. This retries the whole recent backlog each run, so the track
    record is gapless — the point of the sleeve is an HONEST measured scoreboard.
    """
    from datetime import timedelta as _td

    cutoff = (today - _td(days=lookback_days)).isoformat()
    pending = repo.fetch_mover_predictions(unresolved_only=True, limit=2000)
    days = sorted({r["for_day"] for r in pending
                   if cutoff <= r["for_day"] <= today.isoformat()})
    agg = {"resolved": 0, "skipped": 0, "total": 0, "days": len(days)}
    for d in days:
        st = resolve_predictions(repo, store, date.fromisoformat(d), cost_pct)
        for k in ("resolved", "skipped", "total"):
            agg[k] += st[k]
    return agg


def resolve_predictions(repo, store, day: date, cost_pct: float) -> Dict:
    """Fill realized outcomes for predictions made FOR ``day`` (archive, else Yahoo fallback)."""
    rows = repo.fetch_mover_predictions(for_day=day.isoformat(), unresolved_only=True)
    resolved = skipped = 0
    for r in rows:
        px = _open_close(store, r["symbol"], day)
        if px is None:
            skipped += 1
            continue
        open_px, close_px = px
        prev_close = r["prev_close"]
        realized = (close_px - prev_close) / prev_close * 100.0 if prev_close else None
        hit = int(abs(realized) >= 5.0) if realized is not None else None
        dir_ok = None
        if r["pred_dir"] in ("LONG", "SHORT") and realized is not None and hit:
            dir_ok = int((realized > 0) == (r["pred_dir"] == "LONG"))
        pnl = None
        if r["fillable"] and r["pred_dir"] == "LONG" and open_px:
            # entry at open, exit at close, net of round-trip friction — the only leg the
            # Indian cash market lets us actually take.
            pnl = (close_px - open_px) / open_px * 100.0 - cost_pct
        repo.resolve_mover_prediction(
            for_day=day.isoformat(), symbol=r["symbol"], open_px=open_px, close_px=close_px,
            realized_move_pct=realized, hit_big5=hit, dir_correct=dir_ok, sleeve_pnl_pct=pnl,
        )
        resolved += 1
    return {"resolved": resolved, "skipped": skipped, "total": len(rows)}


def annotate_tip_mentions(preds: List[Dict], news_items, hours: int = 24) -> None:
    """Stamp each prediction with the count of tip-channel mentions in the last ``hours``.

    ``news_items``: enriched NewsItems (any provider); only ``telegram:*`` sources count.
    SHADOW FEATURE by design: displayed and tracked, but it never adjusts a probability —
    the sleeve first has to MEASURE whether mentions carry any signal (SPIKE_HUNTER rules;
    tip channels are the documented pump-and-dump vector, so trust must be earned in data).
    """
    from datetime import datetime as _dt, timedelta as _td

    import pytz as _pytz

    cutoff = _dt.now(_pytz.timezone("Asia/Kolkata")) - _td(hours=hours)
    counts: Dict[str, int] = {}
    for it in news_items or []:
        if not str(getattr(it, "source", "")).startswith("telegram:"):
            continue
        if getattr(it, "ts", None) is not None and it.ts < cutoff:
            continue
        for sym in getattr(it, "symbols", []) or []:
            counts[sym] = counts.get(sym, 0) + 1
    for p in preds:
        p["tg_mentions"] = counts.get(p["symbol"], 0)


def predict_for_next_day(repo, store, cal_market, today: date, top_n: int = 10,
                         run_id: Optional[str] = None, news_items=None) -> List[Dict]:
    """Build + persist predictions for the next trading day from today's archive."""
    calib = predictor.load_calibration()
    daily = predictor.update_daily_cache(store, today)
    target = next_trading_day(cal_market, today)
    preds = predictor.build_predictions(daily, calib, target, top_n=top_n)
    if news_items is not None:
        annotate_tip_mentions(preds, news_items)
    # Re-running replaces the target day's unresolved set (resolved rows are history).
    repo.delete_unresolved_mover_predictions(target.isoformat())
    for p in preds:
        repo.save_mover_prediction(p, run_id=run_id)
    return preds


def _qual_dir(p: Dict) -> str:
    """Direction of the qualifying (prev-day) move, from the signed bucket."""
    qd = p.get("qual_dir")
    if qd in ("up", "down", "flat"):
        return qd
    basis = str(p.get("basis", ""))
    if basis.startswith("up"):
        return "up"
    if basis.startswith("dn"):
        return "down"
    return "flat"


def _dir_text(p: Dict) -> str:
    """Sign-aware direction lean — a down-day up-lean is framed as a BOUNCE, never a blind LONG."""
    if p.get("pred_dir") == "NONE" or p.get("p_dir") is None:
        return "direction: coin-flip"
    qd, side = _qual_dir(p), p["pred_dir"]
    frame = ""
    if side == "LONG" and qd == "down":
        frame = "bounce, "
    elif side == "LONG" and qd == "up":
        frame = "continuation, "
    elif side == "SHORT" and qd == "up":
        frame = "fade, "
    elif side == "SHORT" and qd == "down":
        frame = "continuation, "
    return f"lean {side} ({frame}{p['p_dir']*100:.0f}% measured, survivor-biased)"


def _tail_text(p: Dict) -> str:
    """Explicit next-day tail split — the honest direction picture (not a blended P(up))."""
    u, d = p.get("p_next_up_big"), p.get("p_next_down_big")
    if u is None or d is None:
        return ""
    qd = _qual_dir(p)
    lead = "after UP day → " if qd == "up" else ("after DOWN day → " if qd == "down" else "")
    return f"{lead}~{u * 100:.0f}% up / {d * 100:.0f}% down"


def _pred_line(p: Dict) -> str:
    lift = f" ({p['lift']:.1f}x base)" if p.get("lift") is not None else ""
    exp = f", typical ±{p['exp_move_pct']:.1f}%" if p.get("exp_move_pct") is not None else ""
    tail = _tail_text(p)
    tail = f", {tail}" if tail else ""
    warn = f" ⚠ {p['warn']}" if p.get("warn") else ""
    tips = (f" 📣 {p['tg_mentions']} tip-channel mention(s) — unweighted, unverified"
            if p.get("tg_mentions") else "")
    return (f"{p['symbol']}: P(±5%+)={p['p_big5'] * 100:.0f}%{lift}{exp}{tail}, "
            f"{_dir_text(p)}{warn}{tips}")


def format_alert(preds: List[Dict], stats: Optional[Dict] = None) -> str:
    """Telegram message: honest, labeled, no manufactured conviction.

    Leads with the TRADEABLE (fillable, ADV>=₹5cr) names — the only legs the shadow book can
    take — and pushes un-fillable circuit-lockers into a clearly-labeled research-only section.
    """
    if not preds:
        return "Movers watch: no qualifying candidates for tomorrow (all buckets below floor)."
    day = preds[0]["for_day"]
    lines = [f"BIG-MOVERS WATCH — {day} (paper research sleeve, measured base rates)"]

    fillable = [p for p in preds if p.get("fillable")]
    research = [p for p in preds if not p.get("fillable")]

    if fillable:
        lines.append("Tradeable (fillable, ADV≥₹5cr):")
        for p in fillable[:5]:
            lines.append(f"{p.get('rank', '•')}. {_pred_line(p)}")
    else:
        lines.append("No fillable (ADV≥₹5cr) candidates — nothing tradeable tomorrow.")

    if research:
        lines.append(f"Research only — cannot fill at circuit ({len(research)} thin-ADV name(s)):")
        for p in research[:5]:
            lines.append(f"• {_pred_line(p)}")

    lines.append("Paper only. Probabilities = 16y survivor-panel base rates (optimistic "
                 "ceiling); direction is near-unforecastable — treat leans as weak.")
    if stats and stats.get("total"):
        lines.append(f"(Yesterday resolved: {stats['resolved']}/{stats['total']})")
    return "\n".join(lines)
