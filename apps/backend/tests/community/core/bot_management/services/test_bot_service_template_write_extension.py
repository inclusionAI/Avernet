"""BotService keeps the legacy best-effort template policy except for hook rejections.

The template-write seam owns domain validation in the corp overlay.  A normal
bot update must surface that deliberate rejection, while unrelated historical
template failures remain best-effort as before.
"""
from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.plugin_api.bot_template_write_extension import (
    TemplateWriteInvalidInput,
)


def _make_bot_service(repository=None, template_service=None) -> BotService:
    return BotService(
        caller_identity_repo=MagicMock(),
        drm_reader=MagicMock(),
        repository=repository or Mock(),
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        bot_app_grant_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=template_service or MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(
            is_teclaw=MagicMock(return_value=False)
        ),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
        skills_pool_native_creation_policy=MagicMock(select=MagicMock(return_value=None)),
        skill_layout_repository=MagicMock(),
    )


def _bot_record(**overrides):
    record = {
        "id": 1,
        "bot_id": "bot-1",
        "bot_name": "TestBot",
        "bot_desc": "desc",
        "owner_id": "user1",
        "status": "ACTIVE",
        "binding_id": None,
        "ext": {},
        "template_type": "normalCC",
        "active_engine": "claude_code",
    }
    record.update(overrides)
    return record


def _wired_service(template_side_effect):
    repository = Mock()
    repository.get_by_id_and_owner.return_value = _bot_record()
    repository.update_by_owner.return_value = _bot_record(bot_name="Renamed")
    repository.get_by_bot_name.return_value = None

    template_service = MagicMock()
    template_service.exists_template.return_value = True
    template_service.get_template_config.return_value = {}
    template_service.update_template.side_effect = template_side_effect

    service = _make_bot_service(repository, template_service)
    return service, repository, template_service


def test_normal_update_surfaces_template_write_rejection():
    rejection = TemplateWriteInvalidInput("related_bots 目标不可消费")
    service, repository, template_service = _wired_service(rejection)

    with patch.object(service, "_should_trigger_memory_initialization", return_value=False):
        with pytest.raises(TemplateWriteInvalidInput, match="目标不可消费"):
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                bot_name="Renamed",
                template_config={"related_bots": [{"template_key": "bad"}]},
            )

    repository.update_by_owner.assert_not_called()
    template_service.update_template.assert_called_once()


def test_normal_update_still_swallow_unrelated_template_failure():
    service, repository, template_service = _wired_service(RuntimeError("database unavailable"))

    with patch.object(service, "_should_trigger_memory_initialization", return_value=False):
        result = service.update_bot(
            bot_id="bot-1",
            user_id="user1",
            bot_name="Renamed",
            template_config={"unrelated": True},
        )

    repository.update_by_owner.assert_called_once()
    assert result["bot_name"] == "Renamed"
    template_service.update_template.assert_called_once()


def test_restart_surfaces_template_write_rejection_before_allocation():
    service, _, _ = _wired_service(None)
    strategy = MagicMock()
    strategy.apply_restart_extra_configs.side_effect = TemplateWriteInvalidInput(
        "invalid template snapshot"
    )
    with patch(
        "agentclaw.community.core.bot_management.engines.resolve_provisioning",
        return_value=(MagicMock(), strategy),
    ):
        with pytest.raises(TemplateWriteInvalidInput, match="invalid template snapshot"):
            service.restart_bot("bot-1", "user1", extra_configs={"template_config": {}})

    service._restart_lock_repo.acquire.assert_not_called()


def test_restart_keeps_legacy_fallback_for_unrelated_extension_failure():
    from agentclaw.community.core.bot_management.services.bot_service import BotServiceError

    service, _, _ = _wired_service(None)
    strategy = MagicMock()
    strategy.apply_restart_extra_configs.side_effect = RuntimeError("optional extension failed")
    with patch(
        "agentclaw.community.core.bot_management.engines.resolve_provisioning",
        return_value=(MagicMock(), strategy),
    ):
        # Reaching the next lifecycle guard proves unrelated errors still fall
        # back to the stored config, without starting allocation or threads.
        with pytest.raises(BotServiceError, match="has no entity_id"):
            service.restart_bot("bot-1", "user1", extra_configs={"template_config": {}})


@pytest.mark.parametrize("hosted", [False, True])
def test_initial_template_preparation_never_swallows_write_rejection(hosted):
    from agentclaw.community.core.bot_management.engines import (
        HostedWorkspaceNotEligibleError,
    )

    service, _, template_service = _wired_service(None)
    template_service.exists_template.return_value = False
    template_service.create_template.side_effect = TemplateWriteInvalidInput("invalid snapshot")
    strategy = MagicMock()
    strategy.ensure_hosted_workspace.side_effect = (
        TemplateWriteInvalidInput("invalid snapshot")
        if hosted else HostedWorkspaceNotEligibleError("not hosted")
    )
    with patch(
        "agentclaw.community.core.bot_management.engines.resolve_provisioning",
        return_value=(MagicMock(), strategy),
    ):
        with pytest.raises(TemplateWriteInvalidInput, match="invalid snapshot"):
            service._prepare_creation_template(
                bot_id="bot-1", user_id="user1", bot_name="TestBot",
                bot_type="personal", active_engine="claude_code",
                template_type="normalCC", template_config={"k": "v"},
            )
