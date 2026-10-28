"""Persisted Owner != entity exercises real lifecycle code beyond route guards."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService, BotNotFoundError, BotOperationNotAllowedError,
)
from agentclaw.community.core.bot_management.services.cleanup_service import BotCleanupService
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository, SkillSetRepository,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.core.service_bot.services.publish_approval_service import PublishApprovalService
from agentclaw.community.plugin_api.models import BotModel
from tests.community.adapters.http.service_bot.test_publish_handoff_permissions import publication as publication


@pytest.fixture
def lifecycle(publication):
    repo = publication.bots
    # Preserve the existing earliest-Bot deletion protection with a real personal Bot.
    personal = repo.insert({
        "bot_id": "default", "entity_id": "B", "entity_type": "staff",
        "owner_id": "B", "creator_id": "B", "bot_type": "personal",
        "active_engine": "openclaw", "status": "ACTIVE",
    })
    with publication.database.orm_session() as session:
        session.get(BotModel, personal["id"]).gmt_create = datetime.now() - timedelta(days=365)
        session.get(BotModel, publication.pk).active_engine = "openclaw"
    skills = SkillRepository(publication.database)
    skill = skills.create({
        "name": "owned", "git_path": "local://owned", "user_id": "A",
        "bolt_id": "service", "is_public": False,
    })
    desired = Mock()
    desired.purge_bot_installations.return_value = {"skills": 0, "mcps": 0}
    purge = Mock()
    purge.delete.return_value = False
    bot_service = BotService.__new__(BotService)
    bot_service._repository = repo
    bot_service._template_service = Mock()
    bot_service._template_service.get_template.return_value = None
    bot_service._passport_plugin = Mock()
    grants = Mock()
    grants.revoke_all_for_bot.return_value = 0
    bot_service._bot_app_grant_provider = lambda: grants
    bot_service._cleanup_service = BotCleanupService(
        skill_repo=skills, skill_set_repo=SkillSetRepository(publication.database),
        desired_state_repo=desired, startup_script_purge=purge,
    )
    bot_service._bcn_service = Mock()
    record = BotPublishRecord(
        id=13820, source_bot_pk=publication.pk, source_bot_id="service",
        publish_bot_id="service", owner_id="A", name="Service", permission_owner="owner",
        status=PublishStatus.DRAFT, version=1, last_pub_id=0, env="dev", ext={},
    )
    publish_repo = Mock()
    publish_repo.get_by_id.return_value = record
    publish_repo.list_by_source_bot.return_value = [record]
    flow = Mock()
    publish = BotPublishService.__new__(BotPublishService)
    publish._repo = publish_repo
    publish._env = "dev"
    publish._bot_service = bot_service
    publish._publish_flow_service_provider = lambda: flow
    return SimpleNamespace(
        world=publication, bots=repo, bot_service=bot_service, publish=publish,
        record=record, flow=flow, grants=grants, desired=desired,
        skills=skills, skill=skill, personal=personal,
    )


def test_delete_after_handoff_removes_stable_assets_and_preserves_personal_bot(lifecycle):
    value = lifecycle
    assert value.publish.delete_service_bot(13820) is True
    assert value.bots.get_by_pk(value.world.pk) is None
    assert value.bots.get_by_pk(value.personal["id"]) is not None
    assert value.skills.get_by_id(value.skill["id"]) is None
    value.bot_service._passport_plugin.destroy_passport.assert_called_once_with("service", "A")
    assert all(call.kwargs["owner_id"] == "A" for call in value.grants.revoke_all_for_bot.call_args_list)
    value.desired.purge_bot_installations.assert_called_once_with(
        bot_id="service", owner_id="A", env="dev",
    )
    assert value.record.owner_id == "A"


def test_old_locator_resolves_resource_but_does_not_authorize_old_owner_delete(lifecycle):
    bot = lifecycle.bot_service.get_bot_by_address("service", "A")
    assert bot["owner_id"] == "B"
    assert bot["entity_id"] == "A"
    with pytest.raises(BotNotFoundError):
        lifecycle.bot_service.delete_bot("service", "A")
    lifecycle.flow.destroy_publish_history.assert_not_called()
    lifecycle.bot_service._passport_plugin.destroy_passport.assert_not_called()


@pytest.mark.parametrize("state", ["missing", "protected"])
def test_delete_preflight_blocks_before_destroying_version_resources(lifecycle, state):
    with lifecycle.world.database.orm_session() as session:
        bot = session.get(BotModel, lifecycle.world.pk)
        if state == "missing":
            bot.is_delete = 1
        else:
            bot.ext = '{"digital_employee": true}'
    with pytest.raises((BotNotFoundError, BotOperationNotAllowedError)):
        lifecycle.publish.delete_service_bot(13820)
    lifecycle.flow.destroy_publish_history.assert_not_called()
    lifecycle.bot_service._passport_plugin.destroy_passport.assert_not_called()


@pytest.mark.asyncio
async def test_approval_checks_and_new_auditor_use_current_owner(lifecycle):
    with lifecycle.world.database.orm_session() as session:
        session.get(BotModel, lifecycle.world.pk).ext = '{"service_bot_config":{"should_approval":true}}'
    process = Mock()
    publish = Mock()
    approval = PublishApprovalService(
        publish_service=publish, publish_flow_service_provider=lambda: lifecycle.flow,
        process_service=process, bot_service=lifecycle.bot_service, task_queue_service=Mock(),
    )
    result = await approval.check_and_process_should_approval(lifecycle.record, "B")
    assert result.status == "SKIP"
    process.start_approval.assert_not_called()
    approval._start_approval_workflow(lifecycle.record, action="online", operator="collaborator", biz_id="request")
    context = process.start_approval.call_args.kwargs["context"]
    assert context["publish_owner_audit"] == "w[B]"
    approval._finalize_new_approval(
        lifecycle.record, approval_result={"puid": "approval"}, action="online", operator="collaborator",
    )
    assert publish.update_publish_ext.call_args.args[1]["approval"]["owner_id"] == "B"
    assert lifecycle.record.owner_id == "A"


@pytest.mark.asyncio
async def test_build_reads_transferred_bot_and_keeps_artifact_resource_locator(lifecycle):
    from tests.community.core.service_bot.services.publish_flow.test_build_artifact_only import (
        _runner, _SimpleProducer,
    )
    producer = _SimpleProducer()
    observed = Mock(wraps=producer.produce_artifact)
    producer.produce_artifact = observed
    runner, ext = _runner(producer)
    runner._bot_service = lifecycle.bot_service
    ext.owner_id.return_value = "A"
    result = await runner.build_artifact_only(lifecycle.record)
    assert result.migration_path == "/snapshot/1"
    request = observed.call_args.args[0]
    assert request.bot["owner_id"] == "B"
    assert request.bot["entity_id"] == "A"
    behavior = runner._provider_behaviors.resolve.return_value
    assert behavior.stage_build_files.call_args.kwargs["owner_id"] == "A"


@pytest.mark.parametrize("surface", ["publication", "edit_lock"])
@pytest.mark.parametrize("locator", ["A", "B"])
@pytest.mark.parametrize("actor", ["A", "B"])
def test_new_platform_lock_surfaces_keep_locator_separate_from_permission(
    lifecycle, surface, locator, actor,
):
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel
    from agentclaw.community.core.service_bot.errors import ServicePublicationNotFoundError
    from agentclaw.community.core.service_bot.services.service_edit_lock_service import ServiceEditLockService
    from agentclaw.community.core.service_bot.services.service_publication_facade import ServicePublicationFacade

    collaborators = Mock()
    collaborators.get_permission_level.return_value = PermissionLevel.NONE
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(
        lock=None, holder_name=None, has_collaborators=False, is_owner=False,
    )
    if surface == "edit_lock":
        service = ServiceEditLockService(
            bot_repo=lifecycle.bots, collaborator_service=collaborators, lock_service=locks,
        )
    else:
        service = ServicePublicationFacade(
            bot_repo=lifecycle.bots, publish_repo=Mock(), publish_service=lifecycle.publish,
            flow_service=lifecycle.flow, approval_service=Mock(), collaborator_service=collaborators,
            lock_service=locks, bot_service=lifecycle.bot_service, device_service=Mock(),
        )
    if actor == "B":
        assert service.get_lock("service", actor_id=actor, owner_id=locator).need_lock is False
        locks.get_lock_info.assert_called_once_with("service", "B", "B")
    else:
        with pytest.raises(ServicePublicationNotFoundError):
            service.get_lock("service", actor_id=actor, owner_id=locator)
        locks.get_lock_info.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["A", "B"])
async def test_legacy_config_update_keeps_stable_input_and_writes_current_bot(lifecycle, actor):
    from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
    from agentclaw.community.adapters.http.service_bot.router_publish import update_service_bot_config
    from agentclaw.community.adapters.http.service_bot.schemas_publish import UpdateServiceBotConfigRequest

    response = await update_service_bot_config(
        request=UpdateServiceBotConfigRequest(
            bot_id="service", owner_id="A", config_update={"should_approval": True},
        ),
        user=AuthenticatedUser(id=actor, staffId=actor, operatorName=actor),
        bot_service=lifecycle.bot_service,
        __interceptor_request__=lifecycle.world.request,
    )
    bot = lifecycle.bots.get_by_pk(lifecycle.world.pk)
    if actor == "B":
        assert response.success is True
        assert bot["ext"]["service_bot_config"]["should_approval"] is True
    else:
        assert response.status_code == 403
        assert not (bot.get("ext") or {}).get("service_bot_config")
    assert bot["entity_id"] == "A"
    assert bot["owner_id"] == "B"


@pytest.mark.parametrize("current_owner", ["B", "C"])
def test_credential_refresh_finds_all_stages_by_stable_entity_after_repeated_handoff(
    lifecycle, current_owner,
):
    with lifecycle.world.database.orm_session() as session:
        bot = session.get(BotModel, lifecycle.world.pk)
        bot.owner_id = current_owner
        bot.binding_id = 7
        bot.ext = '{"passport":{"agent_code":"agent"}}'
    service = lifecycle.bot_service
    service._bot_publish_repo = Mock()

    def publication(**kwargs):
        assert kwargs["owner_id"] == "A"
        key = "online" if kwargs["publish_status"] == PublishStatus.SUCCESS.value else "verify"
        return SimpleNamespace(id=13820, ext={"binding": {key: 8 if key == "online" else 9}})

    service._bot_publish_repo.get_by_publish_bot_id.side_effect = publication
    service._device_binding_repo = Mock()
    service._device_binding_repo.list_active_caller_instance_bindings.return_value = [
        SimpleNamespace(id=10, device_id="caller"),
    ]
    service._hot_update_by_device_binding = Mock(return_value={"devices": [{"device_id": "draft"}]})
    service._hot_update_by_publish_binding = Mock(return_value={"devices": [{"device_id": "published"}]})
    service._hot_update_by_caller_binding = Mock(return_value={
        "binding_id": 10, "type": "caller", "device_count": 1, "devices": [{"device_id": "caller"}],
    })

    result = service.hot_update_passport_token_to_device("service", current_owner, "unit" * 4)

    assert {item["type"] for item in result["bindings"]} == {"draft", "online", "verify", "caller"}
    service._device_binding_repo.list_active_caller_instance_bindings.assert_called_once_with(
        bot_id="service", owner_id="A", env="dev",
    )
    for method in (
        service._hot_update_by_device_binding, service._hot_update_by_publish_binding,
        service._hot_update_by_caller_binding,
    ):
        assert all(call.kwargs["user_id"] == "A" for call in method.call_args_list)


@pytest.mark.parametrize("locator", ["A", "B"])
@pytest.mark.parametrize("stage", [None, "draft", "success"])
def test_baas_stage_binding_and_outbound_type_resolve_transferred_bot(lifecycle, locator, stage):
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    with lifecycle.world.database.orm_session() as session:
        session.get(BotModel, lifecycle.world.pk).binding_id = 7
    baas = BaasService.__new__(BaasService)
    baas._bot_repo = lifecycle.bots
    baas._bot_publish_repo = Mock()
    baas._bot_publish_repo.get_by_publish_bot_id.return_value = SimpleNamespace(ext={"binding": {"online": 8}})

    assert baas.get_bind_id("service", locator, "service", stage) == (8 if stage == "success" else 7)
    assert baas._resolve_bot_type("service", locator) == "service"
    if stage == "success":
        assert baas._bot_publish_repo.get_by_publish_bot_id.call_args.kwargs["owner_id"] == "A"


@pytest.mark.parametrize("locator", ["A", "B"])
def test_managed_read_only_rules_follow_bot_after_handoff(lifecycle, locator):
    from tests.community.core.service_bot.services.test_baas_service_read_only_rules import _make_composer

    with lifecycle.world.database.orm_session() as session:
        session.get(BotModel, lifecycle.world.pk).ext = '{"read_only_rules":[{"path":"/tmp/owned-rule","rule_type":"file"}]}'
    # Use the real repository; the legacy test helper only adapts Mock repositories.
    from agentclaw.community.core.service_bot.services.deploy.managed_composer import ManagedDeployConfigComposer
    template = _make_composer()
    composer = ManagedDeployConfigComposer(
        storage_path=template._storage_path, sandbox_registry=template._sandbox_registry,
        bot_repo=lifecycle.bots, storage_policy=template._storage_policy,
    )
    result = composer._get_set_read_only_rule(bot_id="service", owner_id=locator, engine="openclaw")
    assert "/tmp/owned-rule" in result
