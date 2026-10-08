"""``resolve_outbound_rule_envelope`` (bootstrap envelope) tests.

The main-chain ``BaasDeviceHeaderUpdater`` calls this engine-neutral function
(located in the engines composition root) at bootstrap to keep a Bot's custom
egress-key when rebuilding the outbound rule. These tests exercise the function
body directly (not the updater) so the changed lines stay covered; the
updater's own wiring is covered separately.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.engines import (
    resolve_outbound_rule_envelope,
)
from agentclaw.community.utils.secret_utils import symmetric_encrypt

_THETA_SECRET = "test_theta_master_key"


class _Secret:
    def __init__(self, value: str):
        self.secret_value = value


class _Resolver:
    def __init__(self, secrets=None):
        self.secrets = secrets or {}

    def get_secret(self, name: str):
        value = self.secrets.get(name)
        if isinstance(value, Exception):
            raise value
        return value


def _template(theta_key):
    return {"bot_template_config": {"ext_config": {"thetaKey": theta_key}}}


def _bot_query(bot):
    """A minimal BotQueryProtocol stub returning ``bot`` for any (id, owner)."""
    q = _BotQueryStub()
    q.bot = bot
    return q


class _BotQueryStub:
    bot = None

    def get_by_id_and_owner(self, bot_id, owner_id):
        return self.bot

    # Other BotQueryProtocol members are unused by the function under test.
    def get_by_binding_id(self, binding_id):
        return None

    def get_by_id(self, bot_id):
        return None

    def list_active_bots_by_entity(self, entity_id, entity_type=None, bot_type=None):
        return []


class _TemplateServiceStub:
    def __init__(self, template_config=None, error=None):
        self._template_config = template_config
        self._error = error

    def get_template_config(self, bot_id):
        if isinstance(self._error, Exception):
            raise self._error
        return self._template_config


def _bot(*, active_engine="aicoding", template_type="normalCC"):
    return {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "bot_type": "personal",
        "active_engine": active_engine,
        "template_type": template_type,
    }


def test_returns_none_when_template_service_not_injected():
    # Default deployment config (community/singlebox): no-op -> legacy fallback.
    result = resolve_outbound_rule_envelope(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_query=_bot_query(_bot()),
        template_service=None,
        secret_resolver=_Resolver({_THETA_SECRET: _Secret("k")}),
        theta_master_key_secret=_THETA_SECRET,
    )
    assert result is None


def test_returns_none_when_bot_not_found():
    result = resolve_outbound_rule_envelope(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_query=_bot_query(None),
        template_service=_TemplateServiceStub(_template("anything")),
        secret_resolver=_Resolver(),
        theta_master_key_secret=_THETA_SECRET,
    )
    assert result is None


def test_returns_none_when_template_service_raises():
    ts = _TemplateServiceStub(error=RuntimeError("store unavailable"))
    result = resolve_outbound_rule_envelope(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_query=_bot_query(_bot()),
        template_service=ts,
        secret_resolver=_Resolver({_THETA_SECRET: _Secret("theta-master-key")}),
        theta_master_key_secret=_THETA_SECRET,
    )
    assert result is None


@pytest.mark.parametrize("active_engine", ["aicoding", "claude_code"])
def test_resolves_custom_theta_key_envelope_happy_path(active_engine):
    # Both coding engines register the same theta-key-consuming strategy, so the
    # bootstrap envelope must be resolved for either (create-bot and restart both
    # go through this path). Covers the success branch (lines 406-430).
    master_key = "theta-master-key"
    resolver = _Resolver({_THETA_SECRET: _Secret(master_key)})
    encrypted = "enc:v1:" + symmetric_encrypt("theta-plaintext", master_key)
    ts = _TemplateServiceStub(_template(encrypted))

    result = resolve_outbound_rule_envelope(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_query=_bot_query(_bot(active_engine=active_engine)),
        template_service=ts,
        secret_resolver=resolver,
        theta_master_key_secret=_THETA_SECRET,
    )

    assert result == {"outbound_api_key": "theta-plaintext"}


def test_returns_none_when_provisioning_strategy_raises():
    # templated engine resolves but build_extra_properties errors -> fail-open.
    class _ExplodingResolver:
        def get_secret(self, name: str):
            raise RuntimeError("boom")

    result = resolve_outbound_rule_envelope(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_query=_bot_query(_bot()),
        template_service=_TemplateServiceStub(_template("enc:v1:ciphertext")),
        secret_resolver=_ExplodingResolver(),
        theta_master_key_secret=_THETA_SECRET,
    )
    assert result is None
