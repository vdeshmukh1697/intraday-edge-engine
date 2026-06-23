"""Tests for Dhan token renewal — offline via injected http_post + tmp .env."""

from __future__ import annotations

import pytest

from signal_engine.brokers.dhan_auth import (
    _extract_token,
    renew_token,
    update_env_token,
)


def test_extract_token_handles_wrapper_shapes():
    assert _extract_token({"accessToken": "AAA"}) == "AAA"
    assert _extract_token({"access_token": "BBB"}) == "BBB"
    assert _extract_token({"data": {"accessToken": "CCC"}}) == "CCC"
    assert _extract_token({"nope": 1}) is None
    assert _extract_token("err") is None


def test_renew_token_returns_fresh_token():
    captured = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["headers"] = headers
        return 200, {"accessToken": "NEW.JWT.TOKEN"}

    tok = renew_token("100123", "OLD.JWT", http_post=fake_post)
    assert tok == "NEW.JWT.TOKEN"
    assert captured["url"].endswith("/RenewToken")
    assert captured["headers"]["access-token"] == "OLD.JWT"
    assert captured["headers"]["dhanClientId"] == "100123"


def test_renew_token_raises_when_no_token():
    def fake_post(url, body, headers):
        return 401, {"errorCode": "DH-901", "message": "Invalid"}

    with pytest.raises(RuntimeError, match="RenewToken returned no token"):
        renew_token("c", "t", http_post=fake_post)


def test_update_env_token_replaces_line_and_backs_up(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SE_DATA_SOURCE=dhan\nDHAN_ACCESS_TOKEN=OLD\nSE_ALERTER=telegram\n")
    update_env_token("FRESH", env_path=env)

    text = env.read_text()
    assert "DHAN_ACCESS_TOKEN=FRESH" in text
    assert "OLD" not in text
    assert "SE_ALERTER=telegram" in text  # other lines preserved
    # backup retains the previous token for recovery
    assert "DHAN_ACCESS_TOKEN=OLD" in (tmp_path / ".env.bak").read_text()


def test_update_env_token_appends_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SE_DATA_SOURCE=dhan\n")
    update_env_token("FRESH", env_path=env)
    assert "DHAN_ACCESS_TOKEN=FRESH" in env.read_text()
