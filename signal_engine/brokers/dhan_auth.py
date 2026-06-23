"""Dhan token lifecycle — automated daily renewal without manual login (PLAN §3.2).

Dhan access tokens are JWTs valid for **24 hours** only. There is no longer-lived token,
but the v2 ``RenewToken`` endpoint issues a fresh 24h token from a still-*active* one — so
a small daily job that renews *before* expiry keeps the engine running unattended with no
browser/2FA step. (The alternative API-key+secret OAuth flow needs an interactive 2FA login
each day and cannot be fully automated — see https://dhanhq.co/docs/v2/authentication/.)

Bootstrapping: generate the first token once in the Dhan web portal. As long as the renew
job runs daily while the token is still valid, it never lapses. If it *does* lapse (engine
off > 24h), RenewToken can't revive it — regenerate once in the portal to re-bootstrap.

All network access is injectable so this is unit-tested offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from signal_engine.obs.logging_setup import get_logger

log = get_logger(__name__)

RENEW_URL = "https://api.dhan.co/v2/RenewToken"

# API-key/secret consent (OTP) flow — the path that powers the in-dashboard login.
CONSENT_GENERATE_URL = "https://auth.dhan.co/app/generate-consent"
CONSENT_LOGIN_URL = "https://auth.dhan.co/login/consentApp-login"
CONSENT_CONSUME_URL = "https://auth.dhan.co/app/consumeApp-consent"


def generate_consent(client_id: str, api_key: str, api_secret: str,
                     http_post: Optional[Callable] = None) -> str:
    """Step 1 of the consent flow: returns a ``consentAppId`` to start a browser login."""
    if not (client_id and api_key and api_secret):
        raise RuntimeError("generate_consent needs client_id + api_key + api_secret")
    post = http_post or _empty_post
    url = f"{CONSENT_GENERATE_URL}?client_id={client_id}"
    status, resp = post(url, None, {"app_id": api_key, "app_secret": api_secret})
    cid = resp.get("consentAppId") if isinstance(resp, dict) else None
    if not cid:
        raise RuntimeError(f"generate-consent failed (HTTP {status}): {resp}")
    return cid


def consent_login_url(consent_app_id: str) -> str:
    """The Dhan-hosted page where the user logs in + enters OTP (Step 2)."""
    return f"{CONSENT_LOGIN_URL}?consentAppId={consent_app_id}"


def consume_consent(token_id: str, api_key: str, api_secret: str,
                    http_post: Optional[Callable] = None) -> str:
    """Step 3: exchange the post-OTP ``tokenId`` for a fresh 24h access token."""
    if not (token_id and api_key and api_secret):
        raise RuntimeError("consume_consent needs token_id + api_key + api_secret")
    post = http_post or _empty_post
    url = f"{CONSENT_CONSUME_URL}?tokenId={token_id}"
    status, resp = post(url, None, {"app_id": api_key, "app_secret": api_secret})
    tok = _extract_token(resp)
    if not tok:
        raise RuntimeError(f"consume-consent failed (HTTP {status}): {resp}")
    log.info("Dhan token minted via consent flow (HTTP %s)", status)
    return tok


def _extract_token(resp: object) -> Optional[str]:
    """Pull the new access token out of Dhan's response (tolerant of wrapper shape)."""
    if not isinstance(resp, dict):
        return None
    if resp.get("accessToken"):
        return resp["accessToken"]
    if resp.get("access_token"):
        return resp["access_token"]
    data = resp.get("data")
    if isinstance(data, dict):
        return data.get("accessToken") or data.get("access_token")
    return None


def renew_token(client_id: str, access_token: str,
                http_post: Optional[Callable] = None) -> str:
    """Rotate an ACTIVE Dhan token for a fresh 24h one. Raises if the API doesn't return one.

    Per Dhan v2 docs, RenewToken is a POST with NO body and exactly two headers
    (``access-token`` + ``dhanClientId``). NOTE (verified 2026-06-23): this returns DH-905 for
    tokens generated in the Dhan **web portal** — it appears to work only for tokens minted via
    the API-key/secret consent flow. The old token is expired server-side on success, so the
    caller MUST persist the returned token immediately (see :func:`update_env_token`).
    """
    if not (client_id and access_token):
        raise RuntimeError("renew_token needs the current client_id + access_token")
    post = http_post or _empty_post
    status, resp = post(RENEW_URL, None, {"access-token": access_token,
                                          "dhanClientId": client_id})
    new = _extract_token(resp)
    if not new:
        raise RuntimeError(f"RenewToken returned no token (HTTP {status}): {resp}")
    log.info("Dhan token renewed (HTTP %s)", status)
    return new


def _empty_post(url: str, _body, headers: dict):  # pragma: no cover - thin network shim
    """POST with no request body (RenewToken's documented contract)."""
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


def update_env_token(new_token: str, env_path: Optional[Path] = None,
                     key: str = "DHAN_ACCESS_TOKEN") -> Path:
    """Rewrite ``key=...`` in the .env file, backing up the previous file first.

    Backup goes to ``<.env>.bak`` so a botched rotation never strands the user without a
    recoverable token. Real process env is not touched (callers update it in-memory).
    """
    from signal_engine.config import REPO_ROOT

    path = env_path or (REPO_ROOT / ".env")
    lines = path.read_text().splitlines() if path.exists() else []
    path.with_suffix(path.suffix + ".bak").write_text("\n".join(lines) + "\n")

    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{key}=") and not replaced:
            out.append(f"{key}={new_token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={new_token}")
    path.write_text("\n".join(out) + "\n")
    return path
