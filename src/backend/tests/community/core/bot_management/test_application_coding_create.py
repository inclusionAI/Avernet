"""Application Coding creation policy and failure-path coverage."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.errors import (
    ApplicationCodingUnavailableError,
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.create_flow import (
    APPLICATION_CODING_ENGINES,
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    complete_bot_authorization,
    create_bot_with_authorization,
    prepare_bot_create,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)

pytestmark = pytest.mark.unit

_CLOUD_PERSONAL = BotCreateContext(
    deployment_mode=BotCreateDeploymentMode.CLOUD,
    space_kind="personal",
)


def _prepare(**overrides):
    params = dict(
        template_type=None,
        template_config=None,
        bot_type="personal",
        engine_type="claude_code",
        context=_CLOUD_PERSONAL,
    )
    params.update(overrides)
    return prepare_bot_create(**params)


def _application_coding_spec(**overrides) -> BotCreateSpec:
    params = dict(
        entity_id="u1",
        engine_type="claude_code",
        bot_type="personal",
        bot_name="Coding Bot",
        template_type="applicationCoding",
        template_config={"devflow_workflow": "x"},
    )
    params.update(overrides)
    return BotCreateSpec(**params)


def test_application_coding_engine_matrix() -> None:
    assert APPLICATION_CODING_ENGINES == frozenset({"claude_code"})


def test_prepare_plain_bot_has_no_hosting_requirement() -> None:
    prepared = _prepare()
    assert prepared.template_config is None
    assert prepared.requires_workspace_hosting is False


def test_prepare_preserves_existing_non_application_template() -> None:
    payload = {"legacy": {"enabled": True}}
    prepared = _prepare(
        template_type="personalCoding",
        template_config=payload,
    )
    assert prepared.template_config == payload
    assert prepared.template_config is not payload
    assert prepared.requires_workspace_hosting is False


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"template_config": {}}, BotTemplateInvalidError),
        (
            {
                "template_type": "applicationCoding",
                "template_config": {},
                "engine_type": "aicoding",
            },
            BotCombinationUnsupportedError,
        ),
        (
            {
                "template_type": "applicationCoding",
                "template_config": {},
                "bot_type": "service",
            },
            BotCombinationUnsupportedError,
        ),
        (
            {
                "template_type": "applicationCoding",
                "template_config": {},
                "context": BotCreateContext(
                    deployment_mode=BotCreateDeploymentMode.CLOUD,
                    space_kind="team",
                ),
            },
            BotCombinationUnsupportedError,
        ),
        (
            {
                "template_type": "applicationCoding",
                "template_config": {},
                "context": BotCreateContext(
                    deployment_mode=BotCreateDeploymentMode.LOCAL,
                    space_kind="personal",
                ),
            },
            BotCombinationUnsupportedError,
        ),
    ],
)
def test_prepare_rejects_invalid_application_coding_combinations(
    overrides, error
) -> None:
    with pytest.raises(error):
        _prepare(**overrides)


def test_prepare_application_coding_without_config_preserves_legacy_default() -> None:
    prepared = _prepare(template_type="applicationCoding")
    assert prepared.template_config is None
    assert prepared.requires_workspace_hosting is True


def test_prepare_application_coding_rejects_supplied_empty_config() -> None:
    with pytest.raises(BotTemplateInvalidError, match="must not be empty"):
        _prepare(template_type="applicationCoding", template_config={})


def test_prepare_application_coding_rejects_known_field_with_wrong_type() -> None:
    with pytest.raises(BotTemplateInvalidError, match="code_repos"):
        _prepare(
            template_type="applicationCoding",
            template_config={"code_repos": "not-a-list"},
        )


def test_prepare_valid_application_coding_returns_detached_config() -> None:
    payload = {"devflow_workflow": "x"}
    prepared = _prepare(
        template_type="applicationCoding",
        template_config=payload,
    )
    assert prepared.template_config == payload
    assert prepared.template_config is not payload
    assert prepared.requires_workspace_hosting is True


def test_shared_create_rejects_missing_hosting_before_passport() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_workspace_hosting_available.return_value = False
    passport = MagicMock()

    with pytest.raises(ApplicationCodingUnavailableError):
        create_bot_with_authorization(
            user_id="u1",
            nick_name="u1",
            bot_id="b1",
            spec=_application_coding_spec(),
            context=_CLOUD_PERSONAL,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
            skill_set_factory=MagicMock(),
        )

    passport.apply_first_agent_passport.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    bot_service.check_create_bot_preflight.assert_not_called()


def test_plain_bot_does_not_query_workspace_hosting() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = True
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {
        "iframe_url": "https://passport/authorize"
    }
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []

    create_bot_with_authorization(
        user_id="u1",
        nick_name="u1",
        bot_id="b1",
        spec=BotCreateSpec(
            entity_id="u1",
            engine_type="openclaw",
            bot_type="personal",
            bot_name="Plain Bot",
        ),
        context=_CLOUD_PERSONAL,
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        skill_set_factory=skill_set_factory,
    )

    bot_service.is_workspace_hosting_available.assert_not_called()


def test_auth_completion_rejects_invalid_combo_before_passport_query() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    passport = MagicMock()

    with pytest.raises(BotCombinationUnsupportedError):
        complete_bot_authorization(
            user_id="u1",
            nick_name="u1",
            bot_id="b1",
            spec=_application_coding_spec(bot_type="service"),
            context=_CLOUD_PERSONAL,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
        )

    passport.query_auth_status.assert_not_called()


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
