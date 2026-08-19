import hashlib
import hmac
import time

import pytest

from agentclaw.community.adapters.http.openapi_v1.task.auth import (
    HmacCallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.core.errors import CallbackAuthError

_SECRET = "s3cr3t"


def _signed(method="POST", path="/openapi/v1/task/callback/workflow_result", body=b'{"x":1}',
            ts=None, secret=_SECRET, token="bcn"):
    ts = ts if ts is not None else str(int(time.time()))
    body_hex = hashlib.sha256(body).hexdigest()
    sign_str = f"{ts}{method}{path}{body_hex}"
    sig = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {
        "X-TaskLoop-Token": token,
        "X-TaskLoop-Timestamp": ts,
        "X-TaskLoop-Signature": sig,
    }


def test_hmac_verify_passes():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET, "claw_mind": "other"})
    h = _signed()
    auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                method="POST", path="/openapi/v1/task/callback/workflow_result")


def test_hmac_bad_signature_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    h = _signed()
    h["X-TaskLoop-Signature"] = "deadbeef"
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                    method="POST", path="/p")


def test_hmac_unknown_source_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    with pytest.raises(CallbackAuthError):
        auth.verify(source="claw_mind", headers={"X-TaskLoop-Timestamp": str(int(time.time()))},
                    raw_body=b"x", method="POST", path="/p")


def test_hmac_stale_timestamp_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET}, max_skew_s=300)
    old_ts = str(int(time.time()) - 1000)
    h = _signed(ts=old_ts)
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":1}',
                    method="POST", path="/p")


def test_hmac_body_tamper_raises():
    auth = HmacCallbackAuthenticator(secrets={"bcn": _SECRET})
    h = _signed(body=b'{"x":1}')
    with pytest.raises(CallbackAuthError):
        auth.verify(source="bcn", headers=h, raw_body=b'{"x":2}',
                    method="POST", path="/p")


def test_noop_always_passes():
    NoopCallbackAuthenticator().verify(source="bcn", headers={}, raw_body=b"anything",
                                       method="POST", path="/p")