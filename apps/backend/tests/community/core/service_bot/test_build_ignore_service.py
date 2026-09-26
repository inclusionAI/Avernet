"""Application authorization and safe boundary logging."""

import logging
from unittest.mock import Mock

import pytest
from agentclaw.community.core.service_bot.services.build_ignore_service import (
    BuildIgnoreService,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.kernel.build_ignore import (
    BuildIgnoreQuery,
    BuildIgnoreCommand,
    BuildIgnoreConfig,
    BuildIgnoreError,
)


@pytest.fixture
def service():
    bots, permissions, rules, registry = Mock(), Mock(), Mock(), Mock()
    bots.get_by_id_and_entity.return_value = dict(
        bot_type="service", active_engine="openclaw", owner_id="owner"
    )
    permissions.get_operable_permission_level.return_value = PermissionLevel.ADMIN
    from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan

    registry.resolve.return_value.get_build_plan.return_value = EngineBuildPlan(
        engine_type="openclaw",
        source_root_name="openclaw",
        migration_subpath="openclaw",
        mcp_config_relpath="mcporter/mcporter.json",
        workspace_subdir="workspace",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
    )
    rules.get.return_value = None
    rules.change.return_value = (BuildIgnoreConfig(("workspace/bin",), 1), True)
    return BuildIgnoreService(bots, permissions, rules, registry, "dev")


@pytest.mark.asyncio
async def test_query_and_change_with_normalized_path(service, caplog):
    caplog.set_level(logging.INFO, logger="start")
    empty = await service.query(
        BuildIgnoreQuery("b", "e", "q"), "owner", is_admin=False
    )
    assert (empty["scope"], empty["revision"], empty["paths"]) == ("build", 0, [])
    changed = await service.change(
        BuildIgnoreCommand("b", "e", "c", "add", "./workspace/bin/"),
        "owner",
        is_admin=True,
    )
    assert changed["paths"] == ["workspace/bin"]
    assert changed["changed"] is True
    assert service.rules.change.call_args.kwargs["path"] == "workspace/bin"
    assert "backend.build_ignore.request" in caplog.text
    assert "backend.build_ignore.response" in caplog.text
    events = {
        record.msg: record.args for record in caplog.records
        if isinstance(record.args, dict) and record.args.get("request_id") == "c"
    }
    request = events["backend.build_ignore.request %s"]
    response = events["backend.build_ignore.response %s"]
    assert request["path"] == "workspace/bin"
    assert request["status"] == "received"
    assert request["elapsed_ms"] == 0
    assert response["status"] == "succeeded"
    assert response["revision"] == 1
    assert response["elapsed_ms"] >= 0
    for event in (request, response):
        assert event["system"] == "backend"
        assert event["route"] == "/api/service-bot/publish/ops/build-ignore"
        assert event["method"] == "POST"
        assert event["operation"] == "add"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,error",
    [
        ("anonymous", "permission_denied"),
        ("missing", "bot_not_found"),
        ("denied", "permission_denied"),
        ("personal", "not_service_bot"),
    ],
)
async def test_rejected_access_never_reads_rules(service, case, error):
    if case == "missing":
        service.bots.get_by_id_and_entity.return_value = None
    if case == "denied":
        service.permissions.get_operable_permission_level.return_value = 0
    if case == "personal":
        service.bots.get_by_id_and_entity.return_value["bot_type"] = "personal"
    with pytest.raises(BuildIgnoreError, match=error):
        await service.query(
            BuildIgnoreQuery("b", "e", "q"),
            "anonymous" if case == "anonymous" else "user",
            is_admin=False,
        )
    service.rules.get.assert_not_called()


@pytest.mark.asyncio
async def test_db_failure_does_not_become_empty_success_or_leak(service, caplog):
    secret = "password=private-test-credential"
    service.rules.get.side_effect = RuntimeError(secret)
    with pytest.raises(RuntimeError):
        await service.query(BuildIgnoreQuery("b", "e", "q"), "owner", is_admin=True)
    assert "backend.build_ignore.failure" in caplog.text
    assert secret not in caplog.text
    event = next(record.args for record in caplog.records
                 if record.msg == "backend.build_ignore.failure %s")
    assert event["status"] == "failed"
    assert event["code"] == "operation_failed"
    assert event["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_required_path_rejected_before_persistence(service):
    with pytest.raises(BuildIgnoreError, match="required_build_path"):
        await service.change(
            BuildIgnoreCommand("b", "e", "c", "add", "mcporter"), "owner", is_admin=True
        )
    service.rules.change.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_path_is_not_echoed_in_boundary_logs(service, caplog):
    caplog.set_level(logging.INFO, logger="start")
    invalid = "workspace/\nFORGED_EVENT"
    with pytest.raises(BuildIgnoreError, match="invalid_ignore_path"):
        await service.change(
            BuildIgnoreCommand("b", "e", "c", "add", invalid),
            "owner", is_admin=True,
        )
    assert "FORGED_EVENT" not in caplog.text
    events = [record.args for record in caplog.records if isinstance(record.args, dict)]
    assert {event["status"] for event in events} == {"received", "failed"}
    assert all(event["path_valid"] is False for event in events)
    service.rules.change.assert_not_called()
