"""Tests for bot create-time provider policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_config import (
    ArcaBotCreateBaasRolloutConfig,
    ArcaBotCreateBaasRolloutConfigProvider,
    ArcaBotCreateBaasRolloutRule,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    BAAS_DEVICE_PROVIDER,
)


class _FakeConfigProvider:
    def __init__(self, config: ArcaBotCreateBaasRolloutConfig) -> None:
        self._config = config

    def get(self) -> ArcaBotCreateBaasRolloutConfig:
        return self._config


def _policy(config: ArcaBotCreateBaasRolloutConfig) -> ArcaBotCreateBaasRolloutPolicy:
    return ArcaBotCreateBaasRolloutPolicy(config_provider=_FakeConfigProvider(config))


class _FakeDRMReader:
    """DRMReaderPlugin double: returns a fixed raw value for any drm_id."""

    def __init__(self, raw_value: str | None = None) -> None:
        self._raw_value = raw_value

    def read(self, drm_id: str) -> str | None:
        return self._raw_value


def _provider(raw_value: str | None = None) -> ArcaBotCreateBaasRolloutConfigProvider:
    """Build the config provider with an injected fake DRM reader."""
    return ArcaBotCreateBaasRolloutConfigProvider(drm_reader=_FakeDRMReader(raw_value))


def test_drm_id_uses_config_field_name():
    assert (
        ArcaBotCreateBaasRolloutConfigProvider.DRM_ID
        == "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "ArcaBotCreateBaasRollout.config,version=3.0@DRM"
    )


def test_disabled_rollout_fails_closed_to_arca():
    decision = _policy(ArcaBotCreateBaasRolloutConfig.disabled()).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "rollout_disabled"


def test_matching_user_bot_type_and_engine_routes_to_baas():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version="v1",
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.reason == "rollout_matched"
    assert decision.rollout_version == "v1"


def test_rollout_decision_is_logged_with_context():
    policy = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version="v1",
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    )

    with patch("agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy.logger.info") as log_info:
        policy.decide(
            user_id="u001",
            bot_type="personal",
            engine_type="openclaw",
            template_type="",
        )

    messages = [str(call.args[0]) for call in log_info.call_args_list if call.args]
    assert any(
        "[arca_to_baas_rollout.decide]" in msg
        and "user_id=u001" in msg
        and "bot_type=personal" in msg
        and "engine_bucket=openclaw" in msg
        and "enabled=True" in msg
        and "version=v1" in msg
        and "target_provider=baas" in msg
        and "reason=rollout_matched" in msg
        for msg in messages
    )


def test_matching_user_group_routes_to_baas():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version="v1",
            user_groups={"baas_pilot": ("u001", "u002")},
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_groups=("baas_pilot",),
                ),
            ),
        )
    ).decide(
        user_id="u002",
        bot_type="personal",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.reason == "rollout_matched"


def test_rule_user_ids_and_user_groups_are_union():
    policy = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version="v1",
            user_groups={"baas_pilot": ("u001",)},
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_groups=("baas_pilot",),
                    allow_user_ids=("extra-user",),
                ),
            ),
        )
    )

    for user_id in ("u001", "extra-user"):
        decision = policy.decide(
            user_id=user_id,
            bot_type="personal",
            engine_type="openclaw",
            template_type="",
        )

        assert decision.target_provider == BAAS_DEVICE_PROVIDER
        assert decision.reason == "rollout_matched"


def test_allow_all_users_routes_matching_combination_to_baas():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version="v1",
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="service",
                    engine_bucket="openclaw",
                    allow_all_users=True,
                ),
            ),
        )
    ).decide(
        user_id="any-user",
        bot_type="service",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.reason == "rollout_matched"


def test_same_user_wrong_bot_type_fails_closed_to_arca():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u001",
        bot_type="service",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "rule_not_found"


def test_matching_rule_but_user_not_allowed_fails_closed_to_arca():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u002",
        bot_type="personal",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "user_not_allowed"


def test_blank_bot_type_falls_back_to_arca():
    policy = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    )

    with patch("agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy.logger.warning") as log_warning:
        decision = policy.decide(
            user_id="u001",
            bot_type="",
            engine_type="openclaw",
            template_type="",
        )

        assert decision.target_provider == ARCA_DEVICE_PROVIDER
        assert decision.reason == "unclassified_branch_fallback"

    log_warning.assert_called_once()


def test_personal_hermes_is_legacy_arca_branch():
    policy = _policy(ArcaBotCreateBaasRolloutConfig.disabled())

    with patch("agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy.logger.warning") as log_warning:
        decision = policy.decide(
            user_id="u001",
            bot_type="personal",
            engine_type="hermes",
            template_type="",
        )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "rollout_disabled"
    assert decision.engine_bucket == "hermes"
    log_warning.assert_not_called()


@pytest.mark.parametrize(
    ("bot_type", "engine_type"),
    [
        ("service", "hermes"),
        ("personal", "moltis"),
        ("personal", "future_engine"),
    ],
)
def test_unclassified_branch_falls_back_to_arca_with_warning(bot_type, engine_type):
    policy = _policy(ArcaBotCreateBaasRolloutConfig.disabled())

    with patch("agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy.logger.warning") as log_warning:
        decision = policy.decide(
            user_id="u001",
            bot_type=bot_type,
            engine_type=engine_type,
            template_type="",
        )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "unclassified_branch_fallback"
    assert decision.engine_bucket == engine_type
    log_warning.assert_called_once()


def test_claude_code_requires_explicit_engine_bucket():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="openclaw",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="claude_code",
        template_type="",
    )

    assert decision.target_provider == ARCA_DEVICE_PROVIDER
    assert decision.reason == "rule_not_found"


def test_claude_code_coding_template_uses_aicoding_bucket():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="personal",
                    engine_bucket="aicoding",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="claude_code",
        template_type="personalCoding",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.engine_bucket == "aicoding"


def test_claude_code_architect_template_uses_aicoding_bucket():
    decision = _policy(
        ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            rules=(
                ArcaBotCreateBaasRolloutRule(
                    bot_type="service",
                    engine_bucket="aicoding",
                    allow_user_ids=("u001",),
                ),
            ),
        )
    ).decide(
        user_id="u001",
        bot_type="service",
        engine_type="claude_code",
        template_type="architect",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.engine_bucket == "aicoding"


def test_claude_code_normalcc_template_keeps_claude_code_bucket():
    assert (
        ArcaBotCreateBaasRolloutPolicy.normalize_engine_bucket(
            engine_type="claude_code",
            template_type="normalCC",
        )
        == "claude_code"
    )


def test_claude_code_missing_template_type_keeps_claude_code_bucket():
    assert (
        ArcaBotCreateBaasRolloutPolicy.normalize_engine_bucket(
            engine_type="claude_code",
            template_type="",
        )
        == "claude_code"
    )

def test_drm_payload_parser_accepts_rule_allow_list():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_all_users": False,
                    "allow_user_ids": ["u001"],
                }
            ],
        }
    )

    assert config.enabled is True
    assert len(config.rules) == 1
    assert config.rules[0] == ArcaBotCreateBaasRolloutRule(
        bot_type="personal",
        engine_bucket="openclaw",
        allow_all_users=False,
        allow_user_ids=("u001",),
    )


def test_drm_provider_get_reads_layotto_json_string(monkeypatch):
    payload = """
    {
      "enabled": "yes",
      "version": "v-string",
      "rules": [
        {
          "bot_type": "personal",
          "engine_bucket": "openclaw",
          "allow_all_users": "on"
        }
      ]
    }
    """

    config = _provider(raw_value=payload).get()

    assert config.enabled is True
    assert config.version == "v-string"
    assert config.rules[0].allow_all_users is True


def test_drm_provider_get_fails_closed_when_drm_unset():
    # DRM 未配置 / 不可用 (reader 返回 None) → 灰度禁用.
    config = _provider(raw_value=None).get()

    assert config.enabled is False


def test_drm_payload_parser_fails_closed_for_empty_raw_values():
    provider = _provider()

    assert provider._parse(None).enabled is False
    assert provider._parse("").enabled is False


def test_drm_payload_parser_fails_closed_for_invalid_json():
    config = _provider()._parse("{not-json")

    assert config.enabled is False


def test_drm_payload_parser_fails_closed_for_json_non_object():
    config = _provider()._parse('["not", "an", "object"]')

    assert config.enabled is False


def test_drm_payload_parser_fails_closed_for_unsupported_raw_type():
    config = _provider()._parse(["not", "a", "dict"])

    assert config.enabled is False


def test_drm_payload_parser_fails_closed_for_disabled_payloads():
    provider = _provider()

    assert provider._parse({"enabled": False}).enabled is False
    assert provider._parse({"enabled": "off"}).enabled is False


def test_drm_payload_parser_accepts_rule_allow_user_group():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
            "user_groups": {"baas_pilot": ["u001", "u002"]},
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_all_users": False,
                    "allow_user_groups": ["baas_pilot"],
                }
            ],
        }
    )

    assert config.enabled is True
    assert config.user_groups == {"baas_pilot": ("u001", "u002")}
    assert config.rules[0] == ArcaBotCreateBaasRolloutRule(
        bot_type="personal",
        engine_bucket="openclaw",
        allow_all_users=False,
        allow_user_groups=("baas_pilot",),
    )


def test_drm_payload_parser_accepts_rule_allow_all_users():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
            "rules": [
                {
                    "bot_type": "service",
                    "engine_bucket": "openclaw",
                    "allow_all_users": True,
                }
            ],
        }
    )

    assert config.enabled is True
    assert config.rules[0].allow_all_users is True
    assert config.rules[0].allow_user_ids == ()


def test_drm_payload_parser_applies_default_scope_to_rules_without_scope():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
            "user_groups": {"baas_pilot": ["u001", "u002"]},
            "default_scope": {
                "allow_all_users": False,
                "allow_user_groups": ["baas_pilot"],
                "allow_user_ids": ["extra-user"],
            },
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                },
                {
                    "bot_type": "service",
                    "engine_bucket": "openclaw",
                },
            ],
        }
    )

    assert config.enabled is True
    assert len(config.rules) == 2
    for rule in config.rules:
        assert rule.allow_all_users is False
        assert rule.allow_user_groups == ("baas_pilot",)
        assert rule.allow_user_ids == ("extra-user",)


def test_drm_payload_parser_rule_scope_overrides_default_scope():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
            "user_groups": {
                "baas_pilot": ["u001"],
                "service_pilot": ["svc001"],
            },
            "default_scope": {
                "allow_user_groups": ["baas_pilot"],
            },
            "rules": [
                {
                    "bot_type": "service",
                    "engine_bucket": "openclaw",
                    "allow_user_groups": ["service_pilot"],
                    "allow_user_ids": ["extra-service-user"],
                },
            ],
        }
    )

    assert config.enabled is True
    assert config.rules[0] == ArcaBotCreateBaasRolloutRule(
        bot_type="service",
        engine_bucket="openclaw",
        allow_user_groups=("service_pilot",),
        allow_user_ids=("extra-service-user",),
    )


def test_drm_payload_parser_rejects_invalid_default_scope():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "user_groups": {"baas_pilot": ["u001"]},
            "default_scope": {
                "allow_user_groups": ["unknown_group"],
            },
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                },
            ],
        }
    )

    assert config.enabled is False


def test_default_scope_can_route_matching_user_to_baas():
    decision = _policy(
        _provider()._parse(
            {
                "enabled": True,
                "version": "v1",
                "user_groups": {"baas_pilot": ["u001"]},
                "default_scope": {"allow_user_groups": ["baas_pilot"]},
                "rules": [
                    {
                        "bot_type": "personal",
                        "engine_bucket": "openclaw",
                    }
                ],
            }
        )
    ).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="openclaw",
        template_type="",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.reason == "rollout_matched"


def test_personal_hermes_drm_rule_can_route_matching_user_to_baas():
    decision = _policy(
        _provider()._parse(
            {
                "enabled": True,
                "version": "v-hermes",
                "rules": [
                    {
                        "bot_type": "personal",
                        "engine_bucket": "hermes",
                        "allow_user_ids": ["u001"],
                    }
                ],
            }
        )
    ).decide(
        user_id="u001",
        bot_type="personal",
        engine_type="hermes",
        template_type="",
    )

    assert decision.target_provider == BAAS_DEVICE_PROVIDER
    assert decision.reason == "rollout_matched"
    assert decision.engine_bucket == "hermes"
    assert decision.rollout_version == "v-hermes"


def test_drm_payload_parser_requires_rules():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "version": "v1",
        }
    )

    assert config.enabled is False


def test_drm_payload_parser_requires_users_when_not_allow_all():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_all_users": False,
                    "allow_user_ids": [],
                }
            ],
        }
    )

    assert config.enabled is False


def test_drm_payload_parser_rejects_missing_user_group_reference():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "user_groups": {"baas_pilot": ["u001"]},
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_user_groups": ["unknown_group"],
                }
            ],
        }
    )

    assert config.enabled is False


def test_drm_payload_parser_rejects_invalid_user_groups():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "user_groups": {"baas_pilot": []},
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_user_groups": ["baas_pilot"],
                }
            ],
        }
    )

    assert config.enabled is False


@pytest.mark.parametrize("engine_bucket", ["moltis", "unknown"])
def test_drm_payload_parser_rejects_non_rollout_engine_bucket(engine_bucket):
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": engine_bucket,
                    "allow_user_ids": ["u001"],
                }
            ],
        }
    )

    assert config.enabled is False


def test_drm_payload_parser_rejects_unregistered_hermes_combination():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "bot_type": "service",
                    "engine_bucket": "hermes",
                    "allow_user_ids": ["u001"],
                }
            ],
        }
    )

    assert config.enabled is False


def test_drm_payload_parser_rejects_missing_or_empty_rule_fields():
    provider = _provider()

    missing_bot_type = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "engine_bucket": "openclaw",
                    "allow_user_ids": ["u001"],
                }
            ],
        }
    )
    empty_bot_type = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "bot_type": "   ",
                    "engine_bucket": "openclaw",
                    "allow_user_ids": ["u001"],
                }
            ],
        }
    )

    assert missing_bot_type.enabled is False
    assert empty_bot_type.enabled is False


def test_drm_payload_parser_rejects_duplicate_combination():
    provider = _provider()
    config = provider._parse(
        {
            "enabled": True,
            "rules": [
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_user_ids": ["u001"],
                },
                {
                    "bot_type": "personal",
                    "engine_bucket": "openclaw",
                    "allow_user_ids": ["u002"],
                },
            ],
        }
    )

    assert config.enabled is False
