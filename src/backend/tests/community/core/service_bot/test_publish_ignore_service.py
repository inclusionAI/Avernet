"""Release targeting and authorization tests; no runtime filesystem access."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from agentclaw.community.api.publish_ignore_service import (
    PublishIgnoreCommand,
    PublishIgnoreError,
)
from agentclaw.community.core.service_bot.services.publish_ignore_service import (
    PublishIgnoreService,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService


@pytest.fixture
def setup():
    command = PublishIgnoreCommand(
        "bot", "entity", "online", "add", "workspace/cache", "req"
    )
    bot = {"id": 17, "bot_type": "service", "owner_id": "owner", "binding_id": 44}
    publication = NS(
        version=3,
        status="success",
        publish_bot_id="botpub3",
        ext={"binding": {"online": 44}},
    )
    binding = NS(
        id=44,
        status="ACTIVE",
        entity_id="entity",
        env="test",
        device_provider="baas",
        device_id="baas-bot",
        device_props={"bolt_id": "bot"},
    )
    bots = Mock(get_by_id_and_entity=Mock(return_value=bot), get_by_id_and_owner=Mock(return_value=bot))
    pubs = Mock(list_by_source_bot=Mock(return_value=[publication]))
    bindings = Mock(get_by_id=Mock(return_value=binding))
    perms = Mock(get_operable_permission_level=Mock(return_value=PermissionLevel.ADMIN))
    runtime = NS(
        targets=AsyncMock(return_value=["a", "b"]),
        change=AsyncMock(return_value={"status": "changed"}),
    )
    resolver = RuntimeBindingResolutionService(
        bot_repository=bots, publish_repository=pubs, binding_repository=bindings,
        caller_instance_repository=Mock(),
    )
    service = PublishIgnoreService(bots, resolver, bindings, perms, runtime, "test")
    return service, command, publication, binding


@pytest.mark.asyncio
async def test_current_stage_collaborator_and_admin(setup, caplog):
    service, command, _, _ = setup
    with caplog.at_level("INFO"):
        result = await service.change(command, "collaborator", is_admin=False)
    assert result["success"] and result["scope"] == "current_instances"
    service.bots.get_by_id_and_owner.assert_called_with("bot", "owner")
    assert [c.args[1] for c in service.runtime.change.call_args_list] == ["a", "b"]
    service.permissions.get_operable_permission_level.assert_called_once()
    await service.change(command, "admin", is_admin=True)
    service.permissions.get_operable_permission_level.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,code",
    [
        ("anonymous", "permission_denied"),
        ("missing_bot", "bot_not_found"),
        ("denied", "permission_denied"),
        ("personal", "not_service_bot"),
        ("stage", "stage_not_bound"),
        ("binding", "stage_not_bound"),
        ("provider", "unsupported_provider"),
        ("empty", "no_current_instances"),
    ],
)
async def test_reject_before_mutation(setup, case, code):
    service, command, pub, binding = setup
    actor = "actor"
    if case == "anonymous":
        actor = "anonymous"
    if case == "missing_bot":
        service.bots.get_by_id_and_entity.return_value = None
    if case == "denied":
        service.permissions.get_operable_permission_level.return_value = (
            PermissionLevel.NONE
        )
    if case == "personal":
        service.bots.get_by_id_and_entity.return_value["bot_type"] = "personal"
    if case == "stage":
        pub.ext = {}
    if case == "binding":
        binding.status = "RELEASED"
    if case == "provider":
        binding.device_provider = "other"
    if case == "empty":
        service.runtime.targets.return_value = []
    with pytest.raises(PublishIgnoreError, match=code):
        await service.change(command, actor, is_admin=False)
    service.runtime.change.assert_not_called()


@pytest.mark.asyncio
async def test_preserves_results_on_replica_failure(setup):
    service, command, _, _ = setup
    service.runtime.change.side_effect = [
        {"status": "changed"},
        {"status": "unknown"},
    ]
    result = await service.change(command, "admin", is_admin=True)
    assert not result["success"]
    assert result["results"][0]["status"] == "changed"
    assert result["results"][1]["status"] == "unknown"


@pytest.mark.asyncio
async def test_service_boundary_logs(setup, monkeypatch):
    from agentclaw.community.core.service_bot.services import (
        publish_ignore_service as module,
    )

    service, command, _, _ = setup
    audit = Mock()
    monkeypatch.setattr(module, "logger", audit)
    await service.change(command, "actor", is_admin=True)
    assert audit.info.call_args_list[0].args[0] == "backend.publish_ignore.request %s"
    response = audit.info.call_args_list[1].args[1]
    assert response["operator_id"] == "actor" and response["stage"] == "online"
    assert response["success"] and "elapsed_ms" in response
    service.bots.get_by_id_and_entity.side_effect = RuntimeError("secret-value")
    with pytest.raises(RuntimeError):
        await service.change(command, "actor", is_admin=True)
    assert audit.warning.call_args.args[0] == "backend.publish_ignore.failure %s"
    assert "secret-value" not in str(audit.mock_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_draft_uses_bot_binding_even_for_caller_bot(setup, provider):
    service, command, pub, binding = setup
    pub.status = "draft"
    pub.ext["binding"]["draft"] = 99
    service.bots.get_by_id_and_entity.return_value["call_type"] = "caller"
    binding.device_provider = provider
    result = await service.change(replace(command, stage="draft"), "collaborator", is_admin=False)
    assert result["success"]
    assert service.runtime.change.call_args.args[0].id == 44
    assert service.runtime.change.call_args.args[3] == "collaborator"
    assert service.runtime.change.call_args.args[0].device_provider == provider


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("missing_bot_binding", "stage_not_bound"),
    ("missing_binding", "stage_not_bound"),
    ("removed_binding", "binding_conflict"),
])
async def test_draft_rejects_invalid_target(setup, case, code):
    service, command, pub, binding = setup
    pub.status = "draft"
    if case == "missing_bot_binding":
        service.bots.get_by_id_and_entity.return_value.pop("binding_id")
    if case == "missing_binding":
        service.bindings.get_by_id.return_value = None
    if case == "removed_binding":
        service.bindings.get_by_id.side_effect = [binding, None]
    with pytest.raises(PublishIgnoreError, match=code):
        await service.change(replace(command, stage="draft"), "owner", is_admin=False)
    service.runtime.change.assert_not_called()
