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


@pytest.fixture
def setup():
    command = PublishIgnoreCommand(
        "bot", "entity", 3, "online", "add", "workspace/cache", "req"
    )
    bot = {"id": 17, "bot_type": "service", "owner_id": "owner"}
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
    bots = Mock(get_by_id_and_entity=Mock(return_value=bot))
    pubs = Mock(list_by_source_bot=Mock(return_value=[publication]))
    bindings = Mock(get_by_id=Mock(return_value=binding))
    perms = Mock(get_operable_permission_level=Mock(return_value=PermissionLevel.ADMIN))
    runtime = NS(
        targets=AsyncMock(return_value=["a", "b"]),
        change=AsyncMock(return_value={"status": "changed"}),
    )
    service = PublishIgnoreService(bots, pubs, bindings, perms, runtime, "test")
    return service, command, publication, binding


@pytest.mark.asyncio
async def test_exact_release_collaborator_and_admin(setup, caplog):
    service, command, _, _ = setup
    with caplog.at_level("INFO"):
        result = await service.change(command, "collaborator", is_admin=False)
    assert result["success"] and result["scope"] == "current_instances"
    service.publications.list_by_source_bot.assert_called_with(17, "test")
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
        ("version", "version_not_unique_or_missing"),
        ("busy", "publication_not_ready"),
        ("restarting", "restart_in_progress"),
        ("stage", "stage_not_bound"),
        ("binding", "binding_conflict"),
        ("provider", "unsupported_provider"),
        ("wrong_bot", "binding_bot_mismatch"),
        ("empty", "no_current_instances"),
        ("operation", "invalid_operation"),
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
    if case == "version":
        command = replace(command, version=4)
    if case == "busy":
        pub.status = "online_pub"
    if case == "restarting":
        pub.ext["restart"] = {"restarting": True}
    if case == "stage":
        pub.ext = {}
    if case == "binding":
        binding.env = "prod"
    if case == "provider":
        binding.device_provider = "other"
    if case == "wrong_bot":
        binding.device_props = {"bolt_id": "another"}
    if case == "empty":
        service.runtime.targets.return_value = []
    if case == "operation":
        command = replace(command, operation="invalid")
    with pytest.raises(PublishIgnoreError, match=code):
        await service.change(command, actor, is_admin=False)
    service.runtime.change.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["change", "error", "partial", "rebinding"])
async def test_preserves_results_on_snapshot_or_replica_failure(setup, snapshot):
    service, command, _, _ = setup
    if snapshot == "rebinding":
        original = service.publications.list_by_source_bot.return_value
        service.publications.list_by_source_bot.side_effect = [
            original,
            [NS(version=3, ext={"binding": {"online": 55}}, status="success")],
        ]
    if snapshot == "change":
        service.runtime.targets.side_effect = [["a"], ["a", "b"]]
    if snapshot == "error":
        service.runtime.targets.side_effect = [["a"], RuntimeError("unavailable")]
    if snapshot == "partial":
        service.runtime.change.side_effect = [
            {"status": "changed"},
            {"status": "unknown"},
        ]
    result = await service.change(command, "admin", is_admin=True)
    assert not result["success"]
    assert result["results"][0]["status"] == "changed"
    if snapshot == "error":
        assert result["snapshot_status"] == "unknown"


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
    assert response["operator_id"] == "actor" and response["version"] == 3
    assert response["success"] and "elapsed_ms" in response
    service.bots.get_by_id_and_entity.side_effect = RuntimeError("secret-value")
    with pytest.raises(RuntimeError):
        await service.change(command, "actor", is_admin=True)
    assert audit.warning.call_args.args[0] == "backend.publish_ignore.failure %s"
    assert "secret-value" not in str(audit.mock_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_draft_uses_source_binding(setup, provider):
    service, command, pub, binding = setup
    pub.status = "draft"
    pub.ext["binding"]["draft"] = 99
    service.bots.get_by_id_and_entity.return_value["binding_id"] = 44
    binding.device_provider = provider
    result = await service.change(replace(command, stage="draft"), "owner", is_admin=False)
    assert result["success"]
    assert all(call.args == (44,) for call in service.bindings.get_by_id.call_args_list)
    assert service.runtime.change.call_args.args[0].device_provider == provider


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("historical", "publication_not_ready"),
    ("multiple", "publication_not_ready"),
    ("missing_binding", "stage_not_bound"),
    ("version", "version_not_unique_or_missing"),
])
async def test_draft_rejects_invalid_target(setup, case, code):
    service, command, pub, _ = setup
    pub.status = "draft"
    service.bots.get_by_id_and_entity.return_value["binding_id"] = 44
    if case == "historical":
        pub.status = "success"
    if case == "multiple":
        service.publications.list_by_source_bot.return_value.append(NS(version=4, status="draft"))
    if case == "missing_binding":
        service.bots.get_by_id_and_entity.return_value.pop("binding_id")
    if case == "version":
        command = replace(command, version=4)
    with pytest.raises(PublishIgnoreError, match=code):
        await service.change(replace(command, stage="draft"), "owner", is_admin=False)
    service.runtime.change.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["binding", "status", "multiple", "missing_bot"])
async def test_draft_snapshot_revalidates_source_and_publication(setup, case):
    service, command, pub, _ = setup
    pub.status = "draft"
    bot = service.bots.get_by_id_and_entity.return_value
    bot["binding_id"] = 44
    if case in {"binding", "missing_bot"}:
        service.bots.get_by_id_and_entity.side_effect = [
            bot, {**bot, "binding_id": 55} if case == "binding" else None,
        ]
    else:
        after = [NS(version=3, status="success" if case == "status" else "draft", ext={})]
        if case == "multiple":
            after.append(NS(version=4, status="draft"))
        service.publications.list_by_source_bot.side_effect = [[pub], after]
    result = await service.change(replace(command, stage="draft"), "owner", is_admin=False)
    assert not result["success"]
    assert result["snapshot_status"] == "changed"
    assert result["results"][0]["status"] == "changed"
