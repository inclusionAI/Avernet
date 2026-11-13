from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_template_resolver import (
    PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
    SystemConfigBaasTemplateResolver,
)


def resolve(entries, engine, *, template_type=None, template_config=None):
    normal = {
        "selectors": [
            {"engine": bucket, "template_uid": bucket}
            for bucket in ("openclaw", "aicoding", "hermes", "claude_code")
        ],
        "templates": {
            bucket: {"template_uuid": f"TEMPLATE-default-{bucket}"}
            for bucket in ("openclaw", "aicoding", "hermes", "claude_code")
        },
    }
    config = MagicMock()
    config.get_config.side_effect = lambda *, category, config_key, env: (
        entries if config_key == PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY else normal
    )
    return SystemConfigBaasTemplateResolver(config).resolve_template(
        env="pre", user_id="staff-a", bot_type="personal", engine_type=engine,
        template_type=template_type, template_config=template_config,
    )


OPENCLAW = {"staff_ids": ["staff-a"], "template_uuid": "TEMPLATE-canary"}


@pytest.mark.parametrize("entries", [OPENCLAW, {"mappings": [OPENCLAW]}])
@pytest.mark.parametrize(("engine", "template_type", "config", "expected"), [
    ("openclaw", None, None, "TEMPLATE-canary"),
    ("aicoding", None, None, "TEMPLATE-default-aicoding"),
    ("hermes", None, None, "TEMPLATE-default-hermes"),
    ("claude_code", None, {"engine_form": "aicoding"}, "TEMPLATE-default-aicoding"),
    ("claude_code", "personalCoding", None, "TEMPLATE-default-aicoding"),
    ("claude_code", "normalCC", None, "TEMPLATE-default-claude_code"),
])
def test_legacy_override_only_applies_to_openclaw(entries, engine, template_type, config, expected):
    assert resolve(entries, engine, template_type=template_type, template_config=config).template_uuid == expected


def test_same_owner_can_have_different_templates_by_runtime_engine():
    coding = {**OPENCLAW, "engine": " AIcoding ", "template_uuid": "TEMPLATE-coding"}
    entries = {"mappings": [OPENCLAW, coding]}
    assert resolve(entries, "openclaw").template_uuid == "TEMPLATE-canary"
    assert resolve(entries, "claude_code", template_config={"engine_form": "aicoding"}).template_uuid == "TEMPLATE-coding"


def test_nonmatching_engine_preserves_explicit_template_selection():
    assert resolve(OPENCLAW, "aicoding", template_config={"template_uid": "hermes"}).template_uuid == "TEMPLATE-default-hermes"


@pytest.mark.parametrize("invalid", [None, "", " ", "*", ["openclaw"], "unknown"])
def test_explicit_invalid_engine_is_not_a_legacy_or_wildcard_mapping(invalid):
    assert resolve({**OPENCLAW, "engine": invalid}, "openclaw").source == "system_config"


def test_conflicting_engine_rules_do_not_cancel_other_engine_overrides():
    coding = {**OPENCLAW, "engine": "aicoding"}
    entries = {"mappings": [coding, OPENCLAW, {**coding, "template_uuid": "TEMPLATE-other"}]}
    assert resolve(entries, "aicoding").template_uuid == "TEMPLATE-default-aicoding"
    assert resolve(entries, "openclaw").template_uuid == "TEMPLATE-canary"
