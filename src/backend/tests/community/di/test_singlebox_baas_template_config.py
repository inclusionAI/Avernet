from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.di.modules.infrastructure.singlebox.template_config import (
    SingleboxBaasTemplateConfigLifecycle,
)


@pytest.mark.asyncio
async def test_seeds_singlebox_baas_template_mapping():
    config_service = MagicMock()
    config_service.get_config.return_value = None
    config_service.set_config.return_value = 7
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="TEMPLATE-local",
    )

    with patch(
        "agentclaw.community.di.modules.infrastructure.singlebox.template_config."
        "env_utils.get_current_env",
        return_value="dev",
    ):
        await lifecycle.startup()

    mapping = config_service.set_config.call_args.kwargs["config_value"]
    assert mapping["templates"]["local_default"]["template_uuid"] == ("TEMPLATE-local")
    assert {item["engine"] for item in mapping["selectors"]} == {
        "openclaw",
        "moltis",
        "hermes",
        "aicoding",
        "claude_code",
    }
    assert config_service.set_config.call_args.kwargs["env"] == "dev"


@pytest.mark.asyncio
async def test_preserves_existing_singlebox_baas_template_mapping():
    config_service = MagicMock()
    config_service.get_config.return_value = {"version": "custom"}
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="TEMPLATE-local",
    )

    await lifecycle.startup()

    config_service.create_category.assert_not_called()
    config_service.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_missing_template_uuid_fails_with_clear_startup_error():
    config_service = MagicMock()
    config_service.get_config.return_value = None
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="",
    )

    with pytest.raises(RuntimeError, match="baas.template_uuid"):
        await lifecycle.startup()


def test_template_uuid_rejects_none_at_construction():
    with pytest.raises(TypeError, match="template_uuid must be str"):
        SingleboxBaasTemplateConfigLifecycle(
            config_service=MagicMock(),
            template_uuid=None,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_migrates_existing_local_template_to_aicoding_alias():
    config_service = MagicMock()
    config_service.get_config.return_value = {
        "version": "custom",
        "templates": {
            "local_default": {"template_uuid": "TEMPLATE-local", "keep": True},
        },
    }
    config_service.set_config.return_value = 8
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="TEMPLATE-local",
    )

    with patch(
        "agentclaw.community.di.modules.infrastructure.singlebox.template_config."
        "env_utils.get_current_env",
        return_value="dev",
    ):
        await lifecycle.startup()

    mapping = config_service.set_config.call_args.kwargs["config_value"]
    assert mapping["templates"]["local_default"] == {"template_uuid": "TEMPLATE-local", "keep": True}
    assert mapping["templates"]["aicoding"] == {"template_uuid": "TEMPLATE-local", "keep": True}
    assert config_service.set_config.call_args.kwargs["env"] == "dev"


@pytest.mark.asyncio
async def test_skips_migration_when_template_alias_already_exists():
    config_service = MagicMock()
    config_service.get_config.return_value = {
        "version": "custom",
        "templates": {
            "local_default": {"template_uuid": "TEMPLATE-local"},
            "aicoding": {"template_uuid": "TEMPLATE-aicoding"},
        },
    }
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="TEMPLATE-local",
    )

    await lifecycle.startup()

    config_service.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_existing_local_template_alias_migration_failure_is_clear():
    config_service = MagicMock()
    config_service.get_config.return_value = {
        "version": "custom",
        "templates": {"local_default": {"template_uuid": "TEMPLATE-local"}},
    }
    config_service.set_config.return_value = None
    lifecycle = SingleboxBaasTemplateConfigLifecycle(
        config_service=config_service,
        template_uuid="TEMPLATE-local",
    )

    with pytest.raises(RuntimeError, match="failed to migrate local BaaS template mapping"):
        await lifecycle.startup()
