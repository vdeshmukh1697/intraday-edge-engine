"""Tests for Dhan token renewal — offline via injected http_post + tmp .env."""

from __future__ import annotations

import pytest

from signal_engine.brokers.dhan_auth import (
    _extract_token,
    _totp_now,
    consent_login_url,
    consume_consent,
    generate_consent,
    generate_token_via_totp,
    renew_token,
    update_env_token,
)


def test_extract_token_handles_wrapper_shapes():
    assert _extract_token({"accessToken": "AAA"}) == "AAA"
    assert _extract_token({"access_token": "BBB"}) == "BBB"
    assert _extract_token({"data": {"accessToken": "CCC"}}) == "CCC"
    assert _extract_token({"nope": 1}) is None
    assert _extract_token("err") is None


def test_totp_matches_rfc6238_vectors():
    # RFC 6238 SHA-1 vectors: secret = base32 of ASCII "12345678901234567890".
    sec = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert _totp_now(sec, for_time=59) == "287082"
    assert _totp_now(sec, for_time=1111111109) == "081804"
    # spaces + lowercase + missing padding are tolerated (as pasted from Dhan's setup screen).
    assert _totp_now("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", for_time=59) == "287082"


def test_generate_token_via_totp_shapes_request_and_extracts_token():
    seen = {}

    def mock_post(url, _body, _headers):
        seen["url"] = url
        return 200, {"accessToken": "FRESH_TOTP_TOKEN"}

    sec = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    tok = generate_token_via_totp("CID42", "9999", sec, http_post=mock_post)
    assert tok == "FRESH_TOTP_TOKEN"
    assert "generateAccessToken" in seen["url"]
    assert all(p in seen["url"] for p in ("dhanClientId=CID42", "pin=9999", "totp="))


def test_generate_token_via_totp_requires_all_fields():
    with pytest.raises(RuntimeError):
        generate_token_via_totp("", "9999", "SECRET")


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


# --- consent (OTP) flow ----------------------------------------------------

def test_generate_consent_returns_consent_id():
    captured = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["headers"] = headers
        return 200, {"consentAppId": "CONSENT123"}

    cid = generate_consent("100123", "APIKEY", "APISECRET", http_post=fake_post)
    assert cid == "CONSENT123"
    assert "client_id=100123" in captured["url"]
    assert captured["headers"] == {"app_id": "APIKEY", "app_secret": "APISECRET"}


def test_consent_login_url_embeds_consent_id():
    assert consent_login_url("CONSENT123").endswith("consentApp-login?consentAppId=CONSENT123")


def test_consume_consent_returns_access_token():
    def fake_post(url, body, headers):
        assert "tokenId=TOK99" in url
        return 200, {"accessToken": "FRESH.JWT"}

    tok = consume_consent("TOK99", "APIKEY", "APISECRET", http_post=fake_post)
    assert tok == "FRESH.JWT"


def test_consume_consent_raises_without_token():
    def fake_post(url, body, headers):
        return 401, {"errorCode": "DH-901"}

    with pytest.raises(RuntimeError, match="consume-consent failed"):
        consume_consent("TOK", "k", "s", http_post=fake_post)
