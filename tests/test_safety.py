"""Safety invariants (PLAN §1, §9, ground rules): this tool NEVER places live orders.

These tests are a guardrail: if anyone ever adds an order-placement path or flips a broker
to live-order mode, CI fails. Decision-support only.
"""

import inspect

from signal_engine.brokers.base import BrokerAdapter
from signal_engine.brokers.dhan import DhanBroker
from signal_engine.brokers.mock import MockBroker


def test_broker_adapter_has_no_order_method():
    # The market-data interface must not expose any order/trade/buy/sell/execute method.
    members = dict(inspect.getmembers(BrokerAdapter, predicate=inspect.isfunction))
    forbidden = ("place_order", "buy", "sell", "execute", "submit_order", "modify_order",
                 "cancel_order", "place")
    for name in members:
        assert name not in forbidden, f"BrokerAdapter must not expose '{name}'"


def test_no_broker_supports_live_orders():
    assert BrokerAdapter.supports_live_orders is False
    assert MockBroker.supports_live_orders is False
    assert DhanBroker.supports_live_orders is False


def test_dhan_adapter_refuses_to_connect_without_explicit_enablement():
    # The live broker is gated: it will not silently start trading or even connect.
    import pytest

    b = DhanBroker(client_id=None, access_token=None)
    with pytest.raises(RuntimeError):
        b.connect()


def test_allow_live_orders_flag_defaults_false():
    from signal_engine.config import EnvConfig

    assert EnvConfig().allow_live_orders is False


def test_no_order_placement_anywhere_in_package():
    """No source file should define an order-placement function (defense in depth)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "signal_engine"
    needles = ("def place_order", "def submit_order", "def buy(", "def sell(", "def execute_order")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, f"order-placement code found: {offenders}"
