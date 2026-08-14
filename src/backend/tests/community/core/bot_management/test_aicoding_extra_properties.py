"""AICoding strategy ``build_extra_properties`` (theta-key consumption) tests.

The generic ``extra_properties`` envelope is the only thing the main chain
threads downstream; the AICoding strategy is the single place that parses /
decrypts engine-owned template fields into it. These tests cover that hook's
resolution + fail-open behaviour so the changed lines stay covered (Rule 23).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)
from agentclaw.community.utils.secret_utils import symmetric_encrypt

# A neutral test-only registry name; the resolver is a fake, so the name is
# only an opaque token threaded through ``build_extra_properties``.
_THETA_SECRET = "test_theta_master_key"


class _Secret:
    def __init__(self, value: str):
        self.secret_value = value


class _Resolver:
    def __init__(self, secrets=None):
        self.secrets = secrets or {}
        self.requested = []

    def get_secret(self, name: str):
        self.requested.append(name)
        value = self.secrets.get(name)
        if isinstance(value, Exception):
            raise value
        return value


def _template(theta_key):
    return {"bot_template_config": {"ext_config": {"thetaKey": theta_key}}}


def _build(resolver, *, theta_key, active_engine="aicoding", template_type="normalCC"):
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=active_engine,
        template_type=template_type,
        template_config=_template(theta_key),
    )
    return AicodingProvisioningStrategy("aicoding").build_extra_properties(
        ctx, secret_resolver=resolver, theta_master_key_secret=_THETA_SECRET
    )


def test_resolves_encrypted_theta_key_into_generic_extra_properties():
    master_key = "theta-master-key"
    resolver = _Resolver({_THETA_SECRET: _Secret(master_key)})
    encrypted = "enc:v1:" + symmetric_encrypt("theta-plaintext", master_key)

    result = _build(resolver, theta_key=encrypted)

    assert result == {"outbound_api_key": "theta-plaintext"}
    assert resolver.requested == [_THETA_SECRET]


def test_legacy_personal_coding_template_type_also_consumes_theta_key():
    master_key = "theta-master-key"
    resolver = _Resolver({_THETA_SECRET: _Secret(master_key)})
    encrypted = "enc:v1:" + symmetric_encrypt("legacy-key", master_key)

    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=None,
        template_type="personalCoding",
        template_config=_template(encrypted),
    )
    result = AicodingProvisioningStrategy("aicoding").build_extra_properties(
        ctx, secret_resolver=resolver, theta_master_key_secret=_THETA_SECRET
    )

    assert result == {"outbound_api_key": "legacy-key"}


@pytest.mark.parametrize("theta_key", [None, "", "plaintext", 123, "enc:v1:"])
def test_missing_or_invalid_theta_key_returns_no_extra_properties(theta_key):
    resolver = _Resolver()

    assert _build(resolver, theta_key=theta_key) is None
    assert resolver.requested == []


def test_empty_master_key_secret_returns_no_extra_properties():
    # Neutral default (community / singlebox / unconfigured): no-op -> legacy fallback.
    resolver = _Resolver({_THETA_SECRET: _Secret("theta-master-key")})
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine="aicoding",
        template_type="normalCC",
        template_config=_template("enc:v1:ciphertext"),
    )
    assert (
        AicodingProvisioningStrategy("aicoding").build_extra_properties(
            ctx, secret_resolver=resolver, theta_master_key_secret=""
        )
        is None
    )
    assert resolver.requested == []


def test_no_secret_resolver_returns_no_extra_properties():
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine="aicoding",
        template_type="normalCC",
        template_config=_template("enc:v1:ciphertext"),
    )
    assert (
        AicodingProvisioningStrategy("aicoding").build_extra_properties(
            ctx, secret_resolver=None, theta_master_key_secret=_THETA_SECRET
        )
        is None
    )


def test_non_coding_engine_does_not_consume_theta_key():
    resolver = _Resolver()

    assert _build(resolver, theta_key="enc:v1:ciphertext", active_engine="openclaw") is None
    assert resolver.requested == []


@pytest.mark.parametrize(
    "secret",
    [None, _Secret(""), RuntimeError("secret store unavailable"), _Secret("wrong-key")],
)
def test_secret_or_decryption_failure_fail_opens_to_no_extra_properties(secret):
    resolver = _Resolver({_THETA_SECRET: secret})

    assert _build(resolver, theta_key="enc:v1:not-valid-ciphertext") is None
