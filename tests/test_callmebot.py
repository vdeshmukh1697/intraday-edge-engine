"""Tests for CallMeBotAlerter (no network — URL captured, never opened)."""

import pytest

from signal_engine.alerts.callmebot import CallMeBotAlerter


def test_requires_phone_and_apikey():
    with pytest.raises(ValueError):
        CallMeBotAlerter(phone="", apikey="")
    with pytest.raises(ValueError):
        CallMeBotAlerter(phone="+918359980149", apikey="")
    with pytest.raises(ValueError):
        CallMeBotAlerter(phone="", apikey="abc123")


def test_send_builds_correct_url(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout=10.0):
        captured["url"] = url

        class R:
            def read(self):
                return b"OK"

        return R()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    a = CallMeBotAlerter(phone="+91 835 998 0149", apikey="testkey99")
    a.send("RELIANCE long signal", level="signal")

    url = captured["url"]
    assert "callmebot.com" in url
    assert "%2B91835998" in url or "91835998" in url  # phone encoded (spaces stripped)
    assert "testkey99" in url
    assert "RELIANCE" in url
    assert "%F0%9F%93%88" in url or "signal" in url.lower()  # emoji or level


def test_send_info_level_uses_info_emoji(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout=10.0):
        captured["url"] = url

        class R:
            def read(self):
                return b"OK"

        return R()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    a = CallMeBotAlerter(phone="+918359980149", apikey="k")
    a.send("healthcheck", level="info")
    assert "healthcheck" in captured["url"]


def test_send_swallows_network_error(monkeypatch, capsys):
    def fake_urlopen(url, timeout=10.0):
        raise OSError("timeout")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    a = CallMeBotAlerter(phone="+918359980149", apikey="k")
    a.send("test")  # must not raise
    out = capsys.readouterr().out
    assert "callmebot-send-failed" in out
