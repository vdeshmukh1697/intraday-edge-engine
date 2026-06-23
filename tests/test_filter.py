"""Hand-verified tests for the liquidity + %-cost filter (PLAN §4.0/§5.4)."""

from __future__ import annotations

import math

from signal_engine.config import CostParams, LiquidityParams
from signal_engine.risk.costs import CostModel
from signal_engine.scan.filter import FilterResult, LiquidityCostFilter
from signal_engine.universe.models import InstrumentMeta


def _filter() -> LiquidityCostFilter:
    return LiquidityCostFilter(LiquidityParams(), CostModel(CostParams()))


def _meta(
    symbol="LIQUID",
    sector="IT",
    avg_daily_turnover_cr=500.0,
    last_price=1000.0,
    est_spread_pct=0.02,
    is_banned=False,
) -> InstrumentMeta:
    return InstrumentMeta(
        symbol=symbol,
        sector=sector,
        avg_daily_turnover_cr=avg_daily_turnover_cr,
        last_price=last_price,
        est_spread_pct=est_spread_pct,
        is_banned=is_banned,
    )


def test_liquid_name_is_tradeable():
    res = _filter().evaluate(_meta(), {"atr_pct": 1.0})
    assert res.tradeable is True
    assert res.reasons == []
    assert res.symbol == "LIQUID"


def test_penny_stock_rejected():
    res = _filter().evaluate(_meta(last_price=10.0))
    assert res.tradeable is False
    assert any("penny" in r for r in res.reasons)


def test_illiquid_rejected():
    res = _filter().evaluate(_meta(avg_daily_turnover_cr=5.0))
    assert res.tradeable is False
    assert any("illiquid" in r for r in res.reasons)


def test_wide_spread_rejected():
    res = _filter().evaluate(_meta(est_spread_pct=0.5))
    assert res.tradeable is False
    assert any("wide spread" in r for r in res.reasons)


def test_banned_rejected():
    res = _filter().evaluate(_meta(is_banned=True))
    assert res.tradeable is False
    assert any("banned" in r for r in res.reasons)


def test_multiple_failures_collects_all():
    res = _filter().evaluate(
        _meta(
            last_price=10.0,
            avg_daily_turnover_cr=5.0,
            est_spread_pct=0.5,
            is_banned=True,
        )
    )
    assert res.tradeable is False
    assert any("banned" in r for r in res.reasons)
    assert any("penny" in r for r in res.reasons)
    assert any("illiquid" in r for r in res.reasons)
    assert any("wide spread" in r for r in res.reasons)
    assert len(res.reasons) == 4


def test_range_below_cost_rejected():
    # breakeven at price 1000 ~= 0.0824% > 0.01% -> range below cost
    res = _filter().evaluate(_meta(), {"atr_pct": 0.01})
    assert res.tradeable is False
    assert "range below cost" in res.reasons


def test_nan_atr_skips_cost_check():
    # NaN atr_pct -> skip cost check; static checks pass -> tradeable.
    res = _filter().evaluate(_meta(), {"atr_pct": float("nan")})
    assert res.tradeable is True
    assert res.reasons == []


def test_features_none_runs_only_static():
    # No features -> cost check skipped even though atr would have failed.
    res = _filter().evaluate(_meta(), features=None)
    assert res.tradeable is True
    assert res.reasons == []


def test_cost_check_uses_finite_breakeven():
    # Sanity-check the hand-computed breakeven the tests rely on.
    breakeven = CostModel(CostParams()).breakeven_pct(1000.0)
    assert math.isfinite(breakeven)
    assert 0.01 < breakeven < 1.0


def test_partition_splits_list():
    flt = _filter()
    metas = [
        _meta(symbol="GOOD"),
        _meta(symbol="PENNY", last_price=10.0),
        _meta(symbol="BANNED", is_banned=True),
    ]
    features = {"GOOD": {"atr_pct": 1.0}}
    tradeable, rejected = flt.partition(metas, features)

    assert [r.symbol for r in tradeable] == ["GOOD"]
    assert {r.symbol for r in rejected} == {"PENNY", "BANNED"}
    assert all(isinstance(r, FilterResult) for r in tradeable + rejected)
    assert all(r.tradeable for r in tradeable)
    assert all(not r.tradeable for r in rejected)
