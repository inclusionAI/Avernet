"""Tests for BotService.update_bot — devflow_workflow change triggers cron update."""
import pytest
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import BotRestartLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.plugin_api.passport import PassportPlugin


def _make_service(
    *,
    cron_provider_return: AsyncMock | None = None,
) -> tuple[BotService, MagicMock]:
    """Build a BotService with minimal real deps and a mock cron provider."""
    mock_repo = MagicMock(spec=BotRepository)
    mock_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot1",
        "user_id": "user1",
        "bot_name": "TestBot",
        "template_type": "applicationCoding",
        "binding_id": None,
        "ext": {},
    }
    mock_repo.update_by_owner.return_value = {
        "bot_id": "bot1",
        "user_id": "user1",
        "bot_name": "TestBot",
        "binding_id": None,
    }

    cron_svc = AsyncMock()
    cron_svc.update_auto_initiate_workflow = AsyncMock(return_value=True)
    if cron_provider_return is not None:
        cron_svc.update_auto_initiate_workflow = cron_provider_return

    cron_provider = MagicMock(return_value=cron_svc)

    service = BotService(
        drm_reader=MagicMock(),
        repository=mock_repo,
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(spec=DeviceBindingRepository),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(spec=OssToNasRecordRepository),
        passport_plugin=MagicMock(spec=PassportPlugin),
        oss_record_repo=MagicMock(spec=OssToNasRecordRepository),
        bot_publish_service_provider=MagicMock(),
        device_service_provider=MagicMock(),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(spec=CollaboratorRepositoryProtocol),
        restart_lock_repo=MagicMock(spec=BotRestartLockRepositoryProtocol),
        teclaw_provision_service_provider=MagicMock(),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=cron_provider,
        policy_service=None,
        baas_template_resolver=None,
    )
    return service, cron_provider


def test_workflow_change_triggers_cron_update():
    """devflow_workflow changes → background thread calls update_auto_initiate_workflow."""
    mock_result = AsyncMock(return_value=True)
    service, cron_provider = _make_service(cron_provider_return=mock_result)

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread") as mock_thread:

        mock_old_tc.return_value = {"devflow_workflow": {"name": "old-flow"}}
        new_template_config = {"devflow_workflow": {"name": "new-flow"}}

        service.update_bot(
            bot_id="bot1",
            user_id="user1",
            template_config=new_template_config,
        )

        # Thread should have been created and started
        mock_thread.assert_called_once()
        thread_kwargs = mock_thread.call_args.kwargs
        assert thread_kwargs["daemon"] is True
        assert "cron-workflow-update-bot1" in thread_kwargs["name"]

        # Manually invoke the thread target to cover the internal code
        target = thread_kwargs["target"]
        target()

        # Verify the cron provider was used
        cron_provider.assert_called_once()
        # Verify update_auto_initiate_workflow was awaited with correct args
        cron_svc = cron_provider.return_value
        cron_svc.update_auto_initiate_workflow.assert_awaited_once_with(
            bot_id="bot1",
            owner_id="user1",
            nick_name="user1",
            new_workflow_name="new-flow",
        )


def test_workflow_change_logs_info():
    """Workflow change logs the transition."""
    mock_result = AsyncMock(return_value=True)
    service, cron_provider = _make_service(cron_provider_return=mock_result)

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread") as mock_thread:

        mock_old_tc.return_value = {"devflow_workflow": {"name": "old-flow"}}
        new_template_config = {"devflow_workflow": {"name": "new-flow"}}

        with patch("agentclaw.community.core.bot_management.services.bot_service.logger") as mock_logger:
            service.update_bot(
                bot_id="bot1",
                user_id="user1",
                template_config=new_template_config,
            )
            # Verify the info log about workflow change
            mock_logger.info.assert_any_call(
                "[bot_service.update_bot] Workflow changed from 'old-flow' to 'new-flow' "
                "for bot bot1, updating cron task in background"
            )


def test_cron_update_exception_in_thread_is_caught():
    """Exception inside _update_cron_workflow is caught and logged as warning."""
    mock_result = AsyncMock(side_effect=RuntimeError("cron service down"))
    service, cron_provider = _make_service(cron_provider_return=mock_result)

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread") as mock_thread:

        mock_old_tc.return_value = {"devflow_workflow": {"name": "old-flow"}}
        new_template_config = {"devflow_workflow": {"name": "new-flow"}}

        service.update_bot(
            bot_id="bot1",
            user_id="user1",
            template_config=new_template_config,
        )

        # Invoke the thread target — exception should be caught, not raised
        target = thread_kwargs = mock_thread.call_args.kwargs
        target_fn = target_kwargs = mock_thread.call_args.kwargs["target"]
        # Should not raise
        target_fn()


def test_cron_update_outer_exception_does_not_break_update_bot():
    """Exception in the outer try/except (e.g. extract_workflow_name fails) doesn't break update_bot."""
    service, cron_provider = _make_service()

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread"):

        # Force an exception inside the outer try block
        mock_old_tc.side_effect = Exception("DB error")

        new_template_config = {"devflow_workflow": {"name": "new-flow"}}

        # Should NOT raise — the outer except catches it
        result = service.update_bot(
            bot_id="bot1",
            user_id="user1",
            template_config=new_template_config,
        )

        assert result is not None


def test_workflow_unchanged_skips_cron_update():
    """devflow_workflow unchanged → no thread spawned."""
    mock_result = AsyncMock(return_value=True)
    service, cron_provider = _make_service(cron_provider_return=mock_result)

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread") as mock_thread:

        mock_old_tc.return_value = {"devflow_workflow": {"name": "same-flow"}}
        new_template_config = {"devflow_workflow": {"name": "same-flow"}}

        service.update_bot(
            bot_id="bot1",
            user_id="user1",
            template_config=new_template_config,
        )

        mock_thread.assert_not_called()


def test_non_application_coding_skips_cron_update():
    """Non-applicationCoding bot → no cron update even if workflow changes."""
    mock_result = AsyncMock(return_value=True)
    service, cron_provider = _make_service(cron_provider_return=mock_result)

    # Override bot to be non-applicationCoding
    service._repository.get_by_id_and_owner.return_value = {
        "bot_id": "bot1",
        "user_id": "user1",
        "bot_name": "TestBot",
        "template_type": "personalCoding",
        "binding_id": None,
        "ext": {},
    }

    with patch.object(service._template_service, "get_template_config") as mock_old_tc, \
         patch.object(service._template_service, "exists_template", return_value=True), \
         patch.object(service._template_service, "update_template"), \
         patch("agentclaw.community.core.bot_management.services.bot_service.threading.Thread") as mock_thread:

        mock_old_tc.return_value = {"devflow_workflow": {"name": "old-flow"}}
        new_template_config = {"devflow_workflow": {"name": "new-flow"}}

        service.update_bot(
            bot_id="bot1",
            user_id="user1",
            template_config=new_template_config,
        )

        mock_thread.assert_not_called()