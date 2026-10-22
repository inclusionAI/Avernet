"""Exercise proof verification through the assembled app, without config overrides."""

import hashlib
import hmac
import json
import time

from agentclaw.community.di.dws_management_config import get_dws_management_config
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from tests.community.framework.di_seams import bind_overrides
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.endpoints.test_openapi_dws_hosting import _Resolver


def seed_proof(body, *, invalid):
    def seed(_world):
        bind_overrides(
            _world,
            SystemConfigServiceProtocol,
            {"get_config": lambda self, **kw: {"mode": "all"}},
        )
        init_principal_verifier_config(_Resolver(), "test-key", strict=False)
        payload = json.dumps(
            {
                "bot_id": "bot",
                "actor_id": "owner",
                "operation": "status",
                "data": {},
                "timestamp_ms": int(time.time() * 1000),
                "nonce": "a" * 32,
            }
        )
        # Build the expiring request at execution, not test discovery time.
        body.update(
            payload=payload,
            signature="0" * 64
            if invalid
            else hmac.new(
                get_dws_management_config().bot_key("bot", "owner"),
                payload.encode(),
                hashlib.sha256,
            ).hexdigest(),
        )

    return seed


for _invalid in (False, True):
    _body = {}
    endpoint_test(
        method="POST",
        path="/internal/dws-hosting/verify-command",
        scenario="invalid_proof" if _invalid else "verified_proof",
        input=CaseInput(json_body=_body),
        seed=seed_proof(_body, invalid=_invalid),
        expect=(
            ExpectError(status=403)
            if _invalid
            else ExpectSuccess(json_equals={"verified": True})
        ),
    )(lambda: None)


def seed_runtime(body):
    def seed(world):
        seed_proof(body, invalid=False)(world)
        value = json.loads(body["payload"])
        value.update(operation="enabled", data={"enabled": True}, timestamp_ms=0)
        body["payload"] = json.dumps(value)
        body["signature"] = hmac.new(
            get_dws_management_config().bot_key("bot", "owner"),
            body["payload"].encode(),
            hashlib.sha256,
        ).hexdigest()

    return seed


_runtime_body = {}
endpoint_test(
    method="POST",
    path="/internal/dws-hosting/runtime-eligibility",
    scenario="revalidate_enable_proof",
    input=CaseInput(json_body=_runtime_body),
    seed=seed_runtime(_runtime_body),
    expect=ExpectSuccess(json_equals={"allowed": True}),
)(lambda: None)
