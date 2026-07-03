"""Plain-English reasons for alerts (2026-07-03 portfolio-manager contract §5).

Every alert the platform sends carries a "why" a non-trader can follow. This module
turns strategy reason codes, premarket/scan vocabulary and runner halt reasons into one
or two short sentences — active voice, no indicator jargon, Indian-format rupees
(₹1,00,000). Pure functions: stdlib only, no I/O, no engine imports — inputs are plain
objects read via ``getattr``/``get`` so runner/scheduler types never leak in here.

Vocabulary sources (map every code they can emit):
  * ``strategies/vwap_ema_adx.py`` — "above VWAP", "below VWAP", "EMA cross up",
    "EMA cross down", "EMA fast>slow", "EMA fast<slow", "ADX 30", "RVOL 2.1x", "RSI 55".
  * ``premarket/scoring.py`` / ``briefing.py`` — drivers "GIFT +0.50%", "US +0.20%",
    "Asia -0.10%", "news +0.50", "ADR +1.2%", "index +0.35%", "prevday -2.10%"; setups
    "momentum", "gap-up momentum", "gap-down momentum", "reversal"; catalysts
    "EARNINGS (+ve news)" (…HIGH_IMPACT events), "ADR +1.2%", "global cues".
  * ``scan/ranking.py`` — no string codes of its own (numeric score); scan picks reuse
    the strategy reasons via :func:`explain_scan_pick`.
  * ``engine/runner.py`` — ``_LossBreaker.halt_reason`` shapes, handled by
    :func:`explain_halt`.

Honesty framing (do not soften): all money is the PAPER book, outcomes are never
promised ("the plan is…", "would…"), and confidence is setup quality — never win-rate.
Every function is exhaustive-fallback: unknown codes become readable generic prose and
nothing here ever raises — a broken sentence must never break an alert send.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "explain_entry",
    "explain_exit",
    "explain_skip",
    "explain_halt",
    "explain_premarket",
    "explain_scan_pick",
    "translate_reasons",
]


# --------------------------------------------------------------------------- #
# Tolerant readers + formatting helpers
# --------------------------------------------------------------------------- #
def _never_raises(fallback: str) -> Callable:
    """Decorator enforcing the §5 contract: never raise, never return empty prose."""

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                out = fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - deliberate: prose must never break an alert
                return fallback
            return out if isinstance(out, str) and out.strip() else fallback

        return wrapper

    return deco


def _get(obj: Any, name: str) -> Any:
    """``getattr`` that also survives raising properties and weird objects."""
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _mget(mapping: Any, key: str) -> Any:
    """Read ``key`` from a dict-like (the ledger money dict) or an attribute object."""
    try:
        if hasattr(mapping, "get"):
            return mapping.get(key)
    except Exception:  # noqa: BLE001
        pass
    return _get(mapping, key)


def _num(value: Any) -> Optional[float]:
    """Coerce to a finite float, else None (NaN/inf/garbage all collapse to None)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _group_inr(digits: str) -> str:
    """en-IN digit grouping: last 3 digits, then pairs — ``1234568`` -> ``12,34,568``."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: List[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def _inr(amount: Any) -> str:
    """Indian-format rupees (₹1,00,000). No decimals above ₹100; up to two (trimmed)
    below it, where paise still matter for stop distances. Never raises."""
    v = _num(amount)
    if v is None:
        v = 0.0
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v > 100:
        return sign + "₹" + _group_inr(str(int(round(v))))
    text = ("%.2f" % v).rstrip("0").rstrip(".")
    return sign + "₹" + (text or "0")


def _trim(value: float) -> str:
    """Percent-style number without noise: 2.10 -> '2.1', 2.00 -> '2'."""
    return ("%.2f" % value).rstrip("0").rstrip(".")


def _enum_text(value: Any) -> str:
    """'LONG' from ``Direction.LONG`` / ``'LONG'`` / enum-ish objects; '' when absent."""
    if value is None:
        return ""
    raw = _get(value, "value")
    if not isinstance(raw, str):
        raw = value if isinstance(value, str) else str(value)
    return raw.strip().upper()


def _side(obj: Any) -> str:
    """'LONG' / 'SHORT' / '' from a plan/pick's ``direction`` or ``bias`` field."""
    for name in ("direction", "bias"):
        text = _enum_text(_get(obj, name))
        if text in ("LONG", "SHORT"):
            return text
    return ""


def _sym(obj: Any, fallback: str = "This stock") -> str:
    value = _get(obj, "symbol")
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _first_target(plan: Any) -> Optional[float]:
    """T1 from ``targets`` (list, T1 first) or a flat ``target`` field."""
    targets = _get(plan, "targets")
    if targets:
        try:
            return _num(targets[0])
        except Exception:  # noqa: BLE001 - non-indexable "targets"
            pass
    return _num(_get(plan, "target"))


# --------------------------------------------------------------------------- #
# Reason-code translation table
# --------------------------------------------------------------------------- #
# Each code maps to (kind, clause): "lead" clauses can open a sentence ("the price is
# above its day-average"), "support" clauses are noun phrases hung off "with …".
_GENERIC_SETUP = "the setup passed the strategy's checklist"
_GENERIC_UNKNOWN = "the setup's other checks passed"

_FIXED: Dict[str, Tuple[str, str]] = {
    # strategies/vwap_ema_adx.py — the "(VWAP)" parenthetical is the one deliberate
    # teaching aid (per the contract example); no other indicator names reach prose.
    "above VWAP": ("lead", "the price is above its day-average (VWAP)"),
    "below VWAP": ("lead", "the price is below its day-average (VWAP)"),
    "EMA cross up": ("lead", "short-term momentum has just crossed above long-term"),
    "EMA cross down": ("lead", "short-term momentum has just crossed below long-term"),
    "EMA fast>slow": ("lead", "short-term momentum is stronger than long-term"),
    "EMA fast<slow": ("lead", "short-term momentum is weaker than long-term"),
    # premarket/scoring.py setups + catch-all catalyst
    "momentum": ("lead", "the plan rides the move already underway"),
    "gap-up momentum": ("lead", "the plan rides a stronger open"),
    "gap-down momentum": ("lead", "the plan rides a weaker open"),
    "reversal": ("lead", "overnight news points against yesterday's move (a turnaround setup)"),
    "global cues": ("lead", "the push comes from overnight global markets"),
}

_EVENT_NOUNS: Dict[str, str] = {
    # premarket/scoring.py HIGH_IMPACT event types
    "EARNINGS": "earnings news",
    "ORDER_WIN": "order-win news",
    "BLOCK_DEAL": "block-deal news",
    "UPGRADE": "analyst-upgrade news",
    "DOWNGRADE": "analyst-downgrade news",
    "LITIGATION": "litigation news",
}

_RE_CATALYST = re.compile(r"^([A-Z][A-Z_]*)\s+\((\+|-)ve news\)$")
_RE_ADR = re.compile(r"^ADR\s+([+-]?[\d.]+)%$")


def _updown(value: float, up: str, down: str, flat: str) -> str:
    if value > 0:
        return up
    if value < 0:
        return down
    return flat


def _clause_adx(m: "re.Match") -> Tuple[str, str]:
    strength = "solid" if float(m.group(1)) >= 25 else "firm"
    return ("support", "a %s trend reading" % strength)


def _clause_rvol(m: "re.Match") -> Tuple[str, str]:
    return ("support", "volume running about %s times its usual pace" % _trim(float(m.group(1))))


def _clause_rsi(_m: "re.Match") -> Tuple[str, str]:
    # RSI only appears when it is NOT stretched against the trade — that's the message.
    return ("support", "momentum not already at an extreme")


def _clause_gift(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "GIFT Nifty is flat overnight")
    return ("lead", "GIFT Nifty points %s%% %s overnight"
            % (_trim(abs(v)), _updown(v, "higher", "lower", "flat")))


def _clause_us(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "US markets closed flat")
    return ("lead", "US markets closed %s %s%%" % (_updown(v, "up", "down", "flat"), _trim(abs(v))))


def _clause_asia(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "Asian markets are flat")
    return ("lead", "Asian markets are %s %s%%" % (_updown(v, "up", "down", "flat"), _trim(abs(v))))


def _clause_news(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "overnight news is neutral")
    return ("lead", "overnight news reads %s" % _updown(v, "positive", "negative", "neutral"))


def _clause_adr(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "its US-listed shares are flat overnight")
    return ("lead", "its US-listed shares moved %s %s%% overnight"
            % (_updown(v, "up", "down", "flat"), _trim(abs(v))))


def _clause_index(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "the broader market looks flat at open")
    return ("lead", "the broader market points %s%% %s at open"
            % (_trim(abs(v)), _updown(v, "higher", "lower", "flat")))


def _clause_prevday(m: "re.Match") -> Tuple[str, str]:
    v = float(m.group(1))
    if v == 0:
        return ("lead", "the stock was flat yesterday")
    return ("lead", "the stock %s %s%% yesterday"
            % (_updown(v, "rose", "fell", "was flat"), _trim(abs(v))))


def _clause_catalyst(m: "re.Match") -> Tuple[str, str]:
    tone = "positive" if m.group(2) == "+" else "negative"
    noun = _EVENT_NOUNS.get(m.group(1), m.group(1).replace("_", " ").lower() + " news")
    return ("lead", "there's %s %s" % (tone, noun))


_PATTERNS: List[Tuple["re.Pattern", Callable]] = [
    (re.compile(r"^ADX\s+([\d.]+)$"), _clause_adx),
    (re.compile(r"^RVOL\s+([\d.]+)x$", re.IGNORECASE), _clause_rvol),
    (re.compile(r"^RSI\s+([\d.]+)$"), _clause_rsi),
    (re.compile(r"^GIFT\s+([+-]?[\d.]+)%$"), _clause_gift),
    (re.compile(r"^US\s+([+-]?[\d.]+)%$"), _clause_us),
    (re.compile(r"^Asia\s+([+-]?[\d.]+)%$"), _clause_asia),
    (re.compile(r"^news\s+([+-]?[\d.]+)$"), _clause_news),
    (_RE_ADR, _clause_adr),
    (re.compile(r"^index\s+([+-]?[\d.]+)%$"), _clause_index),
    (re.compile(r"^prevday\s+([+-]?[\d.]+)%$"), _clause_prevday),
    (_RE_CATALYST, _clause_catalyst),
]


def _translate_one(code: str) -> Tuple[str, Optional[str]]:
    """One reason code -> (kind, clause); kind is 'lead' | 'support' | 'unknown'."""
    text = code.strip()
    if text in _FIXED:
        return _FIXED[text]
    for pattern, build in _PATTERNS:
        m = pattern.match(text)
        if m:
            try:
                return build(m)
            except Exception:  # noqa: BLE001 - malformed number in a code -> generic
                return ("unknown", None)
    return ("unknown", None)


def _join_and(parts: List[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _plain_catalyst(text: Any) -> Optional[str]:
    """Catalyst string (premarket/scoring.py) -> noun phrase, or None when unknown —
    better to stay silent than echo a code."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    m = _RE_CATALYST.match(raw)
    if m:
        tone = "positive" if m.group(2) == "+" else "negative"
        return "%s %s" % (tone, _EVENT_NOUNS.get(m.group(1),
                                                 m.group(1).replace("_", " ").lower() + " news"))
    m = _RE_ADR.match(raw)
    if m:
        v = float(m.group(1))
        return "its US-listed shares %s %s%% overnight" % (
            _updown(v, "up", "down", "flat"), _trim(abs(v)))
    if raw == "global cues":
        return "overnight global cues"
    return None


# --------------------------------------------------------------------------- #
# §5 public functions
# --------------------------------------------------------------------------- #
@_never_raises(_GENERIC_SETUP)
def translate_reasons(reasons: List[str]) -> str:
    """Strategy/premarket reason codes -> ONE flowing plain-English clause.

    Embeddable: lowercase start, no trailing period — callers write
    ``f"{sym} is trending up — {translate_reasons(plan.reasons)}."``. Every code the
    strategy/premarket layers emit is mapped; unknown codes become a readable generic
    and raw codes (``ema9>ema21``…) are NEVER echoed. Empty/None input yields a generic
    setup clause. Never raises.
    """
    leads: List[str] = []
    supports: List[str] = []
    unknown = False
    for item in (reasons or []):
        text = str(item).strip() if item is not None else ""
        if not text:
            continue
        kind, clause = _translate_one(text)
        if kind == "lead" and clause and clause not in leads:
            leads.append(clause)
        elif kind == "support" and clause and clause not in supports:
            supports.append(clause)
        elif kind == "unknown":
            unknown = True

    segments: List[str] = []
    if leads:
        segments.append(_join_and(leads))
    if supports:
        segments.append(("with " if leads else "the setup shows ") + _join_and(supports))
    if unknown:
        segments.append(_GENERIC_UNKNOWN)

    if not segments:
        return _GENERIC_SETUP
    if len(segments) == 1:
        return segments[0]
    if len(segments) == 2:
        return segments[0] + ", " + segments[1]
    return segments[0] + ", " + segments[1] + ", and " + segments[2]


_FALLBACK_ENTRY = ("A new setup fired. The paper portfolio takes it only when the price "
                   "levels and free cash line up.")


@_never_raises(_FALLBACK_ENTRY)
def explain_entry(plan, qty, notional, equity) -> str:
    """Entry alert "Why:" — the setup in plain words, then what the paper book does.

    Matches the contract example: ``RELIANCE is trending up — the price is above its
    day-average (VWAP) …. Buying 17 shares (~₹48,450, 48% of the portfolio), risking
    about ₹391: exit at ₹2,827 if it drops, book profit near ₹2,893.`` With qty 0/None
    it describes the plan only ("would") — nothing is bought. Never promises outcomes.
    """
    sym = _sym(plan)
    side = _side(plan)
    clause = translate_reasons(_get(plan, "reasons") or [])
    if side == "LONG":
        opener = "%s is trending up — %s." % (sym, clause)
    elif side == "SHORT":
        opener = "%s is trending down — %s." % (sym, clause)
    else:
        opener = "%s has set up a trade — %s." % (sym, clause)

    entry = _num(_get(plan, "entry"))
    stop = _num(_get(plan, "stop_loss"))
    target = _first_target(plan)
    exit_cond = "if it rises" if side == "SHORT" else "if it drops"

    q_num = _num(qty)
    q = int(q_num) if q_num is not None and q_num > 0 else 0
    notional_v = _num(notional)
    if notional_v is None and q and entry:
        notional_v = q * entry
    equity_v = _num(equity)
    risk_per_share = abs(entry - stop) if entry is not None and stop is not None else None

    if q:
        shares = "%d share%s" % (q, "" if q == 1 else "s")
        head = ("Selling %s short" % shares) if side == "SHORT" else ("Buying %s" % shares)
        size_bits: List[str] = []
        if notional_v:
            size_bits.append("~" + _inr(notional_v))
            if equity_v and equity_v > 0:
                size_bits.append("%d%% of the portfolio"
                                 % int(round(100.0 * notional_v / equity_v)))
        if size_bits:
            head += " (%s)" % ", ".join(size_bits)
        if risk_per_share is not None:
            head += ", risking about %s" % _inr(q * risk_per_share)
        levels: List[str] = []
        if stop is not None:
            levels.append("exit at %s %s" % (_inr(stop), exit_cond))
        if target is not None:
            levels.append("book profit near %s" % _inr(target))
        action = head + ((": " + ", ".join(levels)) if levels else "") + "."
    else:
        # No sized shares: describe the plan only — the book spends nothing here.
        levels = []
        if entry is not None:
            levels.append("enter near %s" % _inr(entry))
        if stop is not None:
            levels.append("exit at %s %s" % (_inr(stop), exit_cond))
        if target is not None:
            levels.append("book profit near %s" % _inr(target))
        if levels:
            action = "The plan would be to %s." % ", ".join(levels)
        else:
            action = "No shares are sized yet, so this is a heads-up only."

    return opener + " " + action


_FALLBACK_EXIT = "A paper position was closed."


@_never_raises(_FALLBACK_EXIT)
def explain_exit(pos, money: dict) -> str:
    """Exit alert plain line — how the trade ended and what it did to the paper book.

    ``money`` is the ledger's ``on_exit(...)`` dict (qty/charges_inr/pnl_inr/
    equity_after/cash_after). Matches the contract example: ``Sold NTPC's 120 shares at
    the profit target. Made ₹612 after ₹43 charges — the portfolio is now ₹1,00,569
    (cash ₹1,00,569).`` Missing fields degrade gracefully; enum codes never leak.
    """
    sym = _get(pos, "symbol")
    name = str(sym).strip() if sym else ""
    side = _side(pos)
    reason = _enum_text(_get(pos, "exit_reason"))
    qty = _num(_mget(money, "qty"))
    qty_txt = ""
    if qty is not None and qty > 0:
        qty_txt = "%d share%s" % (int(qty), "" if int(qty) == 1 else "s")

    verb = "Bought back" if side == "SHORT" else ("Sold" if side == "LONG" else "Closed")
    if name and qty_txt:
        who = "%s %s's %s" % (verb, name, qty_txt)
    elif name:
        who = "%s %s" % (verb, name)
    else:
        who = "Closed the position"

    tails = {
        "TARGET": " at the profit target",
        "STOP": " at the stop-loss to cap the loss",
        "TIME_STOP": " after it went nowhere for too long",
        "SQUARE_OFF": " in the end-of-day square-off (intraday trades never stay overnight)",
    }
    first = who + tails.get(reason, "") + "."

    pnl = _num(_mget(money, "pnl_inr"))
    if pnl is None:
        return first
    if pnl > 0:
        second = "Made %s" % _inr(pnl)
    elif pnl < 0:
        second = "Lost %s" % _inr(-pnl)
    else:
        second = "Broke even"
    charges = _num(_mget(money, "charges_inr"))
    if charges is not None:
        second += " after %s charges" % _inr(charges)
    equity_after = _num(_mget(money, "equity_after"))
    if equity_after is not None:
        second += " — the portfolio is now %s" % _inr(equity_after)
        cash_after = _num(_mget(money, "cash_after"))
        if cash_after is not None:
            second += " (cash %s)" % _inr(cash_after)
    return first + " " + second + "."


_FALLBACK_SKIP = "A setup was found, but the paper portfolio sits this one out."


@_never_raises(_FALLBACK_SKIP)
def explain_skip(plan, price, cash) -> str:
    """Affordability skip — the setup fired but even one share costs more than the free
    cash, so the paper portfolio deliberately passes (contract example wording)."""
    sym = _sym(plan, fallback="a stock")
    price_v = _num(price)
    if price_v is None:
        price_v = _num(_get(plan, "entry"))
    cash_v = _num(cash)
    if price_v is not None and cash_v is not None:
        middle = "one share costs %s and only %s cash is free" % (_inr(price_v), _inr(cash_v))
    elif price_v is not None:
        middle = "one share costs %s — more than the free cash" % _inr(price_v)
    elif cash_v is not None:
        middle = "only %s cash is free — not enough for even one share" % _inr(cash_v)
    else:
        middle = "there isn't enough free cash for even one share"
    return "Found a setup on %s but %s, so the paper portfolio sits this one out." % (sym, middle)


_FALLBACK_HALT = ("No new trades for the rest of the day — a safety brake tripped. "
                  "Open positions still close normally.")

# engine/runner.py ``_LossBreaker.halt_reason`` shapes.
_RE_HALT_DRAWDOWN = re.compile(
    r"daily drawdown limit hit \(drawdown ([\d.]+)% from peak ([+\-\d.]+)% >= ([\d.]+)%\)"
)
_RE_HALT_LOSSES = re.compile(r"(\d+) consecutive losses >= (\d+)")


@_never_raises(_FALLBACK_HALT)
def explain_halt(reason, session_pnl_pct) -> str:
    """Session-breaker halt in plain words: why new entries stopped for the day. The
    brake protects the paper book; it predicts nothing. Raw reasons are never echoed."""
    text = str(reason).strip() if reason else ""
    m = _RE_HALT_DRAWDOWN.search(text)
    if m:
        body = ("The paper book slipped %s%% from its best point today, which trips the "
                "%s%% daily safety brake, so no new trades for the rest of the day."
                % (m.group(1), m.group(3)))
    else:
        m2 = _RE_HALT_LOSSES.search(text)
        if m2:
            body = ("After %s losing trades in a row, the engine steps aside for the "
                    "rest of the day rather than keep pushing." % m2.group(1))
        else:
            body = ("A safety brake tripped, so the engine takes no new trades for the "
                    "rest of the day.")
    body += " Open positions still close normally."
    pnl = _num(session_pnl_pct)
    if pnl is not None:
        body += " Today's paper result so far: %+.2f%%." % pnl
    return body


_HEADS_UP = "This is a heads-up, money moves only when the live engine actually enters."
_FALLBACK_PREMARKET = "No pre-market read this morning. " + _HEADS_UP


@_never_raises(_FALLBACK_PREMARKET)
def explain_premarket(outlook, top_pick) -> str:
    """Morning briefing summary — index mood plus the best idea, sized against the paper
    book. Matches the contract example: ``Market looks slightly positive at open (global
    cues up). Best idea: INFY long — if taken, the plan would put about ₹22,000 of the
    ₹1,00,000 book on it. This is a heads-up, …``. Predictions are suggestions only."""
    gap = _num(_get(outlook, "expected_gap_pct"))
    bias = _enum_text(_get(outlook, "gap_bias"))
    tone = _enum_text(_get(outlook, "risk_tone"))
    cues = {"RISK_ON": "global cues up", "RISK_OFF": "global cues down"}.get(
        tone, "global cues mixed")

    if bias == "GAP_UP" or (not bias and gap is not None and gap > 0):
        word = "positive"
    elif bias == "GAP_DOWN" or (not bias and gap is not None and gap < 0):
        word = "negative"
    elif bias == "FLAT" or gap is not None:
        word = "flat"
    else:
        word = ""
    if word == "flat":
        first = "Market looks flat at open (%s)." % cues
    elif word:
        if gap is not None and abs(gap) < 0.5:
            strength = "slightly "
        elif gap is not None and abs(gap) >= 1.0:
            strength = "strongly "
        else:
            strength = ""
        first = "Market looks %s%s at open (%s)." % (strength, word, cues)
    else:
        first = "No overnight read on the market this morning."

    if top_pick is None:
        return first + " No stand-out single stock idea. " + _HEADS_UP

    sym = _sym(top_pick, fallback="")
    side_word = {"LONG": "long", "SHORT": "short"}.get(_side(top_pick), "")
    label = " ".join(part for part in (sym, side_word) if part) or "one stock"
    catalyst = _plain_catalyst(_get(top_pick, "catalyst"))
    cat_txt = (" on %s" % catalyst) if catalyst else ""
    notional_v = _num(_get(top_pick, "notional"))
    equity_v = _num(_get(top_pick, "portfolio_equity"))
    if equity_v is None:
        equity_v = _num(_get(top_pick, "equity"))
    if notional_v and equity_v:
        second = ("Best idea: %s%s — if taken, the plan would put about %s of the %s "
                  "book on it." % (label, cat_txt, _inr(notional_v), _inr(equity_v)))
    elif notional_v:
        second = ("Best idea: %s%s — if taken, the plan would put about %s of the book "
                  "on it." % (label, cat_txt, _inr(notional_v)))
    else:
        second = "Best idea: %s%s." % (label, cat_txt)
    return first + " " + second + " " + _HEADS_UP


_SUGGESTION = "Suggestion only — nothing is bought or sold until the live engine takes it."
_FALLBACK_SCAN = "A scan pick surfaced without a complete price plan. " + _SUGGESTION


@_never_raises(_FALLBACK_SCAN)
def explain_scan_pick(plan_like) -> str:
    """Scan/leaderboard pick in plain words plus "risking ₹X to make about ₹Y". The scan
    never spends the book's cash, so the suggestion framing is always attached."""
    sym = _sym(plan_like)
    side_word = {"LONG": "long", "SHORT": "short"}.get(_side(plan_like), "trade")
    clause = translate_reasons(_get(plan_like, "reasons") or [])
    first = "%s looks worth watching for a %s: %s." % (sym, side_word, clause)

    entry = _num(_get(plan_like, "entry"))
    stop = _num(_get(plan_like, "stop_loss"))
    target = _first_target(plan_like)
    q_num = _num(_get(plan_like, "qty"))
    q = int(q_num) if q_num is not None and q_num > 0 else 0
    notional_v = _num(_get(plan_like, "notional"))
    risk_ps = abs(entry - stop) if entry is not None and stop is not None else None
    reward_ps = abs(target - entry) if entry is not None and target is not None else None

    middle = ""
    if risk_ps is not None and reward_ps is not None:
        if q:
            scale = (" (about %s on it)" % _inr(notional_v)) if notional_v else ""
            middle = (" If taken, the plan would risk about %s to make about %s%s."
                      % (_inr(q * risk_ps), _inr(q * reward_ps), scale))
        else:
            middle = (" If taken, the plan would risk about %s a share to make about %s."
                      % (_inr(risk_ps), _inr(reward_ps)))
    elif notional_v:
        middle = " If taken, the plan would put about %s of the book on it." % _inr(notional_v)
    return first + middle + " " + _SUGGESTION
