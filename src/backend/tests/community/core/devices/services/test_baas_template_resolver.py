"""Tests for BaaS template uid -> uuid resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_template_resolver import (
    BAAS_TEMPLATE_MAPPING_CATEGORY,
    BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
    PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
    BaasTemplateResolveError,
    SystemConfigBaasTemplateResolver,
)


def _resolver(config_value):
    system_config = MagicMock()
    system_config.get_config.return_value = config_value
    return SystemConfigBaasTemplateResolver(system_config), system_config


def test_resolves_template_uid_and_uuid_from_system_config():
    resolver, system_config = _resolver(
        {
            "version": "pre-001",
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "openclaw",
                    "template_type": "normalCC",
                    "template_uid": "openclaw_personal_default",
                }
            ],
            "templates": {
                "openclaw_personal_default": {
                    "template_uuid": "TEMPLATE-openclaw-personal"
                }
            },
        }
    )

    template_uid = resolver.resolve_template_uid(
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type="normalCC",
        template_config=None,
    )
    template_uuid = resolver.resolve_template_uuid(
        env="pre",
        template_uid=template_uid,
    )

    assert template_uid == "openclaw_personal_default"
    assert template_uuid == "TEMPLATE-openclaw-personal"
    system_config.get_config.assert_any_call(
        category="system",
        config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
        env="pre",
    )


def test_resolves_template_uid_and_uuid_together():
    resolver, _ = _resolver(
        {
            "version": "pre-001",
            "selectors": [
                {
                    "engine": "claude_code",
                    "template_uid": "claude_default",
                }
            ],
            "templates": {
                "claude_default": {
                    "template_uuid": "TEMPLATE-claude-default"
                }
            },
        }
    )

    template = resolver.resolve_template(
        bot_id="bot001",
        user_id="user001",
        env="pre",
        bot_type="service",
        engine_type="claude_code",
        template_type="normalCC",
        template_config=None,
    )

    assert template.template_uid == "claude_default"
    assert template.template_uuid == "TEMPLATE-claude-default"
    assert template.source == "system_config"
    assert template.config_version == "pre-001"


def test_explicit_template_uid_wins_but_uuid_still_comes_from_system_config():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "openclaw",
                    "template_uid": "openclaw_personal_default",
                }
            ],
            "templates": {
                "openclaw_personal_default": {
                    "template_uuid": "TEMPLATE-openclaw-personal"
                },
                "custom_business_uid": {
                    "template_uuid": "TEMPLATE-custom-business"
                },
            },
        }
    )

    template_uid = resolver.resolve_template_uid(
        env="prod",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config={"template_uid": "custom_business_uid"},
    )

    assert template_uid == "custom_business_uid"
    assert (
        resolver.resolve_template_uuid(env="prod", template_uid=template_uid)
        == "TEMPLATE-custom-business"
    )


def test_invalid_explicit_template_uid_fails_fast():
    resolver, _ = _resolver({"selectors": [], "templates": {}})

    with pytest.raises(BaasTemplateResolveError, match="invalid explicit"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config={"template_uid": "  "},
        )


def test_claude_code_coding_template_maps_to_aicoding_engine():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "aicoding",
                    "template_type": "personalCoding",
                    "template_uid": "aicoding_personal_default",
                }
            ],
            "templates": {
                "aicoding_personal_default": {
                    "template_uuid": "TEMPLATE-aicoding-personal"
                }
            },
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="claude_code",
            template_type="personalCoding",
            template_config=None,
        )
        == "aicoding_personal_default"
    )


def test_claude_code_architect_template_maps_to_aicoding_engine():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "service",
                    "engine": "aicoding",
                    "template_type": "architect",
                    "template_uid": "aicoding_architect_template",
                }
            ],
            "templates": {
                "aicoding_architect_template": {
                    "template_uuid": "TEMPLATE-aicoding-architect"
                }
            },
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="pre",
            bot_type="service",
            engine_type="claude_code",
            template_type="architect",
            template_config=None,
        )
        == "aicoding_architect_template"
    )


def test_claude_code_normalcc_template_keeps_claude_code_engine():
    assert (
        SystemConfigBaasTemplateResolver.normalize_engine_for_template(
            engine_type="claude_code",
            template_type="normalCC",
        )
        == "claude_code"
    )


def test_claude_code_missing_template_type_keeps_claude_code_engine():
    assert (
        SystemConfigBaasTemplateResolver.normalize_engine_for_template(
            engine_type="claude_code",
            template_type=None,
        )
        == "claude_code"
    )


def test_claude_code_non_normalcc_template_type_maps_to_aicoding_case_insensitive():
    assert (
        SystemConfigBaasTemplateResolver.normalize_engine_for_template(
            engine_type="claude-code",
            template_type=" Architect ",
        )
        == "aicoding"
    )

def test_resolves_personal_hermes_template_uid():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "hermes",
                    "template_uid": "hermes_personal_default",
                }
            ],
            "templates": {
                "hermes_personal_default": {
                    "template_uuid": "TEMPLATE-hermes-personal"
                }
            },
        }
    )

    template_uid = resolver.resolve_template_uid(
        env="prod",
        bot_type="personal",
        engine_type="hermes",
        template_type=None,
        template_config=None,
    )

    assert template_uid == "hermes_personal_default"
    assert (
        resolver.resolve_template_uuid(env="prod", template_uid=template_uid)
        == "TEMPLATE-hermes-personal"
    )


def test_selector_without_bot_type_matches_all_supported_bot_types():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "engine": "openclaw",
                    "template_uid": "default_bot_template",
                }
            ],
            "templates": {},
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )
    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="service",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )


def test_more_specific_bot_type_selector_wins_over_generic_selector():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "engine": "openclaw",
                    "template_uid": "default_bot_template",
                },
                {
                    "bot_type": "service",
                    "engine": "openclaw",
                    "template_uid": "service_bot_template",
                },
            ],
            "templates": {},
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="service",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "service_bot_template"
    )


def test_more_specific_template_type_selector_wins_over_generic_selector():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "engine": "aicoding",
                    "template_uid": "aicoding_bot_template",
                },
                {
                    "engine": "aicoding",
                    "template_type": "applicationCoding",
                    "template_uid": "application_coding_template",
                },
            ],
            "templates": {},
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="claude_code",
            template_type="applicationCoding",
            template_config=None,
        )
        == "application_coding_template"
    )


def test_legacy_template_whitelist_selector_wins_for_matching_openclaw_user():
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": "cluster/template_whitelist",
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "openclaw",
                "template_uid": "default_bot_template",
            },
        ],
        "templates": {},
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else {"template_id": "ARCA-TEMPLATE-ZHIMA", "staff_ids": ["100016"]}
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id="100016",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "openclaw_zhima_whitelist_template"
    )


def test_legacy_template_whitelist_selector_falls_back_when_user_not_listed():
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": "cluster/template_whitelist",
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "openclaw",
                "template_uid": "default_bot_template",
            },
        ],
        "templates": {},
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else {"template_id": "ARCA-TEMPLATE-ZHIMA", "staff_ids": ["100014"]}
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id="100016",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )


def test_openclaw_legacy_whitelist_selector_does_not_override_coding_template():
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": "cluster/template_whitelist",
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "aicoding",
                "template_uid": "aicoding_bot_template",
            },
        ],
        "templates": {},
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else {"template_id": "ARCA-TEMPLATE-ZHIMA", "staff_ids": ["100016"]}
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id="100016",
            bot_type="personal",
            engine_type="claude_code",
            template_type="applicationCoding",
            template_config=None,
        )
        == "aicoding_bot_template"
    )


def test_selector_ignores_non_dict_and_non_matching_dimensions():
    resolver, _ = _resolver(
        {
            "selectors": [
                "bad-selector",
                {
                    "bot_type": "service",
                    "engine": "openclaw",
                    "template_uid": "service_bot_template",
                },
                {
                    "engine": "openclaw",
                    "template_type": "personalCoding",
                    "template_uid": "coding_template",
                },
                {
                    "engine": "openclaw",
                    "template_uid": "default_bot_template",
                },
            ],
            "templates": {},
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type="normalCC",
            template_config=None,
        )
        == "default_bot_template"
    )


@pytest.mark.parametrize(
    ("legacy_reference", "whitelist_config"),
    [
        ("bad-reference", {"staff_ids": ["100016"]}),
        ("/template_whitelist", {"staff_ids": ["100016"]}),
        ("cluster/", {"staff_ids": ["100016"]}),
        ("cluster/template_whitelist", None),
        ("cluster/template_whitelist", {"staff_ids": "100016"}),
    ],
)
def test_legacy_template_whitelist_bad_config_falls_back(
    legacy_reference, whitelist_config
):
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": legacy_reference,
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "openclaw",
                "template_uid": "default_bot_template",
            },
        ],
        "templates": {},
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id="100016",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )


def test_legacy_template_whitelist_missing_user_id_falls_back_without_reading_legacy_config():
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": "cluster/template_whitelist",
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "openclaw",
                "template_uid": "default_bot_template",
            },
        ],
        "templates": {},
    }
    system_config = MagicMock()
    system_config.get_config.return_value = mapping
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id=None,
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )
    system_config.get_config.assert_called_once_with(
        category="system",
        config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
        env="prod",
    )


def test_legacy_template_whitelist_read_failure_falls_back():
    mapping = {
        "selectors": [
            {
                "engine": "openclaw",
                "legacy_template_whitelist": "cluster/template_whitelist",
                "template_uid": "openclaw_zhima_whitelist_template",
            },
            {
                "engine": "openclaw",
                "template_uid": "default_bot_template",
            },
        ],
        "templates": {},
    }

    def get_config(*, category, config_key, env):
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY:
            return mapping
        raise RuntimeError("legacy config down")

    system_config = MagicMock()
    system_config.get_config.side_effect = get_config
    resolver = SystemConfigBaasTemplateResolver(system_config)

    assert (
        resolver.resolve_template_uid(
            env="prod",
            user_id="100016",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )


def test_missing_selector_fails_fast():
    resolver, _ = _resolver({"selectors": [], "templates": {}})

    with pytest.raises(BaasTemplateResolveError, match="selector"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_invalid_mapping_shape_fails_fast():
    resolver, _ = _resolver(None)

    with pytest.raises(BaasTemplateResolveError, match="missing or invalid"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_system_config_read_failure_fails_fast():
    system_config = MagicMock()
    system_config.get_config.side_effect = RuntimeError("system_config down")
    resolver = SystemConfigBaasTemplateResolver(system_config)

    with pytest.raises(BaasTemplateResolveError, match="failed to read"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_generic_selector_can_match_unknown_bot_type():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "engine": "openclaw",
                    "template_uid": "default_bot_template",
                }
            ],
            "templates": {},
        }
    )

    assert (
        resolver.resolve_template_uid(
            env="prod",
            bot_type="desktop",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )
        == "default_bot_template"
    )


def test_missing_selectors_list_fails_fast():
    resolver, _ = _resolver({"templates": {}})

    with pytest.raises(BaasTemplateResolveError, match="selectors list"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_invalid_selector_template_uid_fails_fast():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "openclaw",
                    "template_uid": "",
                }
            ],
            "templates": {},
        }
    )

    with pytest.raises(BaasTemplateResolveError, match="invalid template_uid"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_ambiguous_selectors_fail_fast():
    resolver, _ = _resolver(
        {
            "selectors": [
                {
                    "bot_type": "personal",
                    "engine": "openclaw",
                    "template_uid": "openclaw_personal_default",
                },
                {
                    "bot_type": "personal",
                    "engine": "openclaw",
                    "template_uid": "openclaw_personal_alt",
                },
            ],
            "templates": {},
        }
    )

    with pytest.raises(BaasTemplateResolveError, match="ambiguous"):
        resolver.resolve_template_uid(
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type=None,
            template_config=None,
        )


def test_missing_templates_object_fails_fast():
    resolver, _ = _resolver({"selectors": []})

    with pytest.raises(BaasTemplateResolveError, match="templates object"):
        resolver.resolve_template_uuid(
            env="prod",
            template_uid="openclaw_personal_default",
        )


def test_missing_template_uid_mapping_fails_fast():
    resolver, _ = _resolver({"selectors": [], "templates": {}})

    with pytest.raises(BaasTemplateResolveError, match="uid not configured"):
        resolver.resolve_template_uuid(
            env="prod",
            template_uid="openclaw_personal_default",
        )


def test_blank_template_uuid_fails_fast():
    resolver, _ = _resolver(
        {
            "selectors": [],
            "templates": {
                "openclaw_personal_default": {"template_uuid": "  "}
            },
        }
    )

    with pytest.raises(BaasTemplateResolveError, match="invalid BaaS template_uuid"):
        resolver.resolve_template_uuid(
            env="prod",
            template_uid="openclaw_personal_default",
        )


def test_invalid_template_uuid_fails_fast():
    resolver, _ = _resolver(
        {
            "selectors": [],
            "templates": {
                "openclaw_personal_default": {"template_uuid": "not-template"}
            },
        }
    )

    with pytest.raises(BaasTemplateResolveError, match="template_uuid"):
        resolver.resolve_template_uuid(
            env="prod",
            template_uid="openclaw_personal_default",
        )


def test_whitelist_override_hit_returns_override_uuid():
    """When user is in whitelist, resolve_template returns override UUID directly."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    whitelist_config = {
        "staff_ids": ["405935"],
        "template_uuid": "TEMPLATE-override-test",
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-override-test"
    assert result.template_uid == "__whitelist_override__"
    assert result.source == "whitelist"


def test_whitelist_miss_falls_through_to_normal_resolution():
    """When user is NOT in whitelist, resolve_template falls through to normal resolution."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    whitelist_config = {
        "staff_ids": ["168944"],
        "template_uuid": "TEMPLATE-override-test",
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-default"
    assert result.source == "system_config"


def test_whitelist_config_missing_does_not_block():
    """When whitelist config key does not exist, normal resolution proceeds."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else None
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-default"
    assert result.source == "system_config"


def test_whitelist_config_read_error_does_not_block():
    """When whitelist config read raises, normal resolution proceeds."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }

    def get_config(*, category, config_key, env):
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY:
            return mapping
        raise RuntimeError("system_config down")

    system_config = MagicMock()
    system_config.get_config.side_effect = get_config
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-default"
    assert result.source == "system_config"


def test_whitelist_override_hit_for_service_bot():
    """Service bot owners in the whitelist use the configured override UUID."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    whitelist_config = {
        "staff_ids": ["405935"],
        "template_uuid": "TEMPLATE-service-override",
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="service",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-service-override"
    assert result.template_uid == "__whitelist_override__"
    assert result.source == "whitelist"


def test_whitelist_override_miss_for_service_bot_falls_through():
    """Service bot owners outside the whitelist keep normal template routing."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    whitelist_config = {
        "staff_ids": ["168944"],
        "template_uuid": "TEMPLATE-service-override",
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="service",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-default"
    assert result.source == "system_config"


def test_resolve_template_override_reads_existing_database_config():
    """Restart lookups reuse the same system_config record as creation."""
    system_config = MagicMock()
    system_config.get_config.return_value = {
        "staff_ids": [405935],
        "template_uuid": "TEMPLATE-shared-config",
    }
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template_override(
        env="pre",
        user_id="405935",
        bot_type="service",
    )

    assert result == "TEMPLATE-shared-config"
    system_config.get_config.assert_called_once_with(
        category=BAAS_TEMPLATE_MAPPING_CATEGORY,
        config_key=PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
        env="pre",
    )


def test_resolve_template_override_skips_unsupported_bot_type():
    """Unsupported bot types preserve their original path without a DB read."""
    system_config = MagicMock()
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template_override(
        env="pre",
        user_id="405935",
        bot_type="desktop",
    )

    assert result is None
    system_config.get_config.assert_not_called()


def test_whitelist_staff_ids_type_coercion():
    """Whitelist matches when staff_ids has integers and user_id is string."""
    mapping = {
        "selectors": [{"engine": "openclaw", "template_uid": "default_template"}],
        "templates": {"default_template": {"template_uuid": "TEMPLATE-default"}},
    }
    whitelist_config = {
        "staff_ids": [405935],
        "template_uuid": "TEMPLATE-override-test",
    }
    system_config = MagicMock()
    system_config.get_config.side_effect = lambda *, category, config_key, env: (
        mapping
        if config_key == BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY
        else whitelist_config
    )
    resolver = SystemConfigBaasTemplateResolver(system_config)

    result = resolver.resolve_template(
        bot_id="bot001",
        user_id="405935",
        env="pre",
        bot_type="personal",
        engine_type="openclaw",
        template_type=None,
        template_config=None,
    )

    assert result.template_uuid == "TEMPLATE-override-test"
    assert result.source == "whitelist"
