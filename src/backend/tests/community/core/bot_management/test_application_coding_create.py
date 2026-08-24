"""Coverage tests for the applicationCoding create failure paths.

The CI changed-line gate flags the create preflight error branches and the
create_bot workspace/template failure handling; these drive exactly those new
lines.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.openapi_v1.bots.router import (
    _application_coding_preflight,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    ApplicationCodingUnavailableError,
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)


def _mock_bot_service(hosting_available: bool = True) -> MagicMock:
    svc = MagicMock()
    svc.is_workspace_hosting_available.return_value = hosting_available
    return svc


# ── create preflight error branches ──────────────────────────────────────


def test_preflight_plain_bot_returns_none() -> None:
    assert (
        _application_coding_preflight(
            template_type=None,
            template_config=None,
            bot_type="personal",
            engine="claude_code",
            space_kind="personal",
            bot_service=_mock_bot_service(),
        )
        is None
    )


def test_preflight_config_without_type_is_rejected() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _application_coding_preflight(
            template_type=None,
            template_config={"devflow_workflow": "x"},
            bot_type="personal",
            engine="claude_code",
            space_kind="personal",
            bot_service=_mock_bot_service(),
        )


def test_preflight_unsupported_type_is_rejected() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _application_coding_preflight(
            template_type="personalCoding",
            template_config={"devflow_workflow": "x"},
            bot_type="personal",
            engine="claude_code",
            space_kind="personal",
            bot_service=_mock_bot_service(),
        )


def test_preflight_application_coding_without_config_is_rejected() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _application_coding_preflight(
            template_type="applicationCoding",
            template_config=None,
            bot_type="personal",
            engine="claude_code",
            space_kind="personal",
            bot_service=_mock_bot_service(),
        )


def test_preflight_wrong_engine_is_rejected() -> None:
    with pytest.raises(BotCombinationUnsupportedError):
        _application_coding_preflight(
            template_type="applicationCoding",
            template_config={"devflow_workflow": "x"},
            bot_type="personal",
            engine="aicoding",  # internal adapter, not an external engine
            space_kind="personal",
            bot_service=_mock_bot_service(),
        )


def test_preflight_missing_hosting_is_rejected() -> None:
    with pytest.raises(ApplicationCodingUnavailableError):
        _application_coding_preflight(
            template_type="applicationCoding",
            template_config={"devflow_workflow": "x"},
            bot_type="personal",
            engine="claude_code",
            space_kind="personal",
            bot_service=_mock_bot_service(hosting_available=False),
        )


def test_preflight_valid_application_coding_passes_through() -> None:
    payload = {"devflow_workflow": "app-flow"}
    result = _application_coding_preflight(
        template_type="applicationCoding",
        template_config=payload,
        bot_type="personal",
        engine="claude_code",
        space_kind="personal",
        bot_service=_mock_bot_service(),
    )
    assert result == payload


# ── create_bot workspace/template failure ────────────────────────────────


def _create_bot_service() -> BotService:
    service = BotService(
        drm_reader=MagicMock(),
        repository=MagicMock(),
        allocation_config=MagicMock(mode="multi", max_devices_per_entity=5),
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
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(
            is_teclaw=MagicMock(return_value=False)
        ),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )
    # Drive create_bot past the pre-device gates so it reaches Step 1.5.
    service.check_create_bot_preflight = MagicMock()
    service._check_device_limit = MagicMock()
    service._resolve_bot_name = MagicMock(return_value="app-coding-bot")
    service._repository.get_by_id_and_owner = MagicMock(return_value=None)
    service._repository.insert = MagicMock(
        return_value={"id": 1, "bot_id": "b1", "owner_id": "u1"}
    )
    service._repository.soft_delete_by_owner = MagicMock()
    return service


def _create(service: BotService) -> None:
    service.create_bot(
        user_id="u1",
        nick_name="n",
        bot_id="b1",
        bot_type="personal",
        engine_type="claude_code",
        template_type="applicationCoding",
        template_config={"devflow_workflow": "x"},
    )


def test_workspace_creation_exception_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        side_effect=RuntimeError("boom")
    )
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_workspace_creation_falsy_return_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        return_value=None
    )
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_template_creation_failure_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        return_value="ws-1"
    )
    svc._template_service.create_template = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_is_workspace_hosting_available() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = None
    assert svc.is_workspace_hosting_available() is False
    svc._workspace_hosting_service = MagicMock()
    assert svc.is_workspace_hosting_available() is True