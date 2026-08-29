from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)


def _service(stored_config):
    service = MagicMock()
    service.get_template_config.return_value = stored_config
    return service


def _ctx(active_engine="claude_code"):
    return BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=active_engine,
        template_type="architect",
    )


def _refresh(stored_config, latest, *, active_engine="claude_code"):
    template_service = _service(stored_config)
    AicodingProvisioningStrategy(active_engine).apply_restart_extra_configs(
        _ctx(active_engine),
        {"template_config": latest} if latest is not None else None,
        template_service=template_service,
    )
    return template_service


def test_restart_persists_matching_newer_template_snapshot():
    latest = {
        "template_key": "architect",
        "template_uid": "aicoding_bot_template",
        "template_version_id": 101,
        "template_version": "V2",
        "bot_template_config": {"id": 101},
    }

    service = _refresh(
        {
            "template_key": "architect",
            "template_uid": "aicoding_bot_template",
            "template_version_id": 100,
        },
        latest,
    )

    service.update_template.assert_called_once_with(
        bot_id="bot-1",
        template_config=latest,
        template_type="architect",
        active_engine="claude_code",
    )


def test_restart_keeps_stored_snapshot_for_equal_or_lower_version():
    for incoming_version in (99, 100):
        service = _refresh(
            {
                "template_key": "architect",
                "template_uid": "aicoding_bot_template",
                "template_version_id": 100,
            },
            {
                "template_key": "architect",
                "template_version_id": incoming_version,
            },
        )

        service.update_template.assert_not_called()


def test_restart_ignores_missing_or_invalid_template_snapshot():
    for latest in (None, {}, {"template_key": "architect"}):
        service = _refresh(
            {
                "template_key": "architect",
                "template_uid": "aicoding_bot_template",
                "template_version_id": 100,
            },
            latest,
        )

        service.update_template.assert_not_called()


def test_restart_uses_active_engine_without_template_type_matching():
    service = _refresh(
        {
            "template_key": "architect",
            "template_uid": "aicoding_bot_template",
            "template_version_id": 100,
        },
        {
            "template_key": "another-template",
            "template_version_id": 101,
        },
    )

    service.update_template.assert_called_once()


def test_non_template_engine_does_not_refresh():
    service = _refresh(
        {"template_version_id": 100},
        {"template_version_id": 101},
        active_engine="openclaw",
    )

    service.get_template_config.assert_not_called()
    service.update_template.assert_not_called()


def test_restart_persists_resync_marker_for_confirmed_template_update():
    latest = {
        "template_key": "architect",
        "template_uid": "aicoding_bot_template",
        "template_version_id": 101,
    }
    template_service = _service({"template_version_id": 100})

    AicodingProvisioningStrategy("claude_code").apply_restart_extra_configs(
        _ctx("claude_code"),
        {"template_config": latest, "confirmed_template_update": True},
        template_service=template_service,
    )

    persisted = template_service.update_template.call_args.kwargs["template_config"]
    assert persisted is not latest
    assert persisted["template_version_id"] == 101
    assert persisted["_aicoding_restart"] == {
        "resync_authorization": True,
        "template_version_id": 101,
        "source": "restart_template_update",
    }
    assert "_aicoding_restart" not in latest
