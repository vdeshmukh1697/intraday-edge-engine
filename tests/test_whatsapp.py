"""Tests for the WhatsApp alerter (Phase 6). No real network — urlopen is monkeypatched."""

import json

import pytest

from signal_engine.alerts.whatsapp import WhatsAppAlerter


def test_requires_credentials():
    with pytest.raises(ValueError):
        WhatsAppAlerter("", "", "")
    with pytest.raises(ValueError):
        WhatsAppAlerter("pid", "tok", "")


def test_send_builds_correct_request(monkeypatch):
    captured = {}

    class _Resp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    WhatsAppAlerter("PHONE123", "TOKENXYZ", "919999999999").send("TCS LONG conf 80", level="signal")

    assert "PHONE123/messages" in captured["url"]
    assert captured["headers"]["authorization"] == "Bearer TOKENXYZ"
    body = captured["body"]
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "919999999999"
    assert body["type"] == "text"
    assert "TCS LONG conf 80" in body["text"]["body"]


def test_send_never_raises_on_network_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    # Must not raise — a failed alert can't be allowed to crash the engine.
    WhatsAppAlerter("p", "t", "to").send("hello")


def test_factory_builds_whatsapp(monkeypatch):
    monkeypatch.setenv("SE_ALERTER", "whatsapp")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "pid")
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_TO", "919999999999")
    from signal_engine.config import load_config
    from signal_engine.factory import build_alerter

    alerter = build_alerter(load_config())
    assert isinstance(alerter, WhatsAppAlerter)
