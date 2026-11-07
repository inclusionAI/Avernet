"""Exercise the real Caller policies against stable, keyed collaboration locks."""
from types import SimpleNamespace

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError, CallerIdentityStage, CallerLockEpochError,
    DraftCallTypeMutationResult, McpCallType,
)
from tests.community.core.caller_identity.test_service import _bot, _service
from tests.community.core.caller_identity.test_iam_token_service import (
    _service as iam_service, _context, _request,
)
from agentclaw.community.core.runtime_binding.models import RuntimeBindingTarget


def _handoff_service(holder):
    bot = _bot()
    bot.update(owner_id="new", entity_id="original")
    svc, deps = _service(bot=bot)
    lock = None if holder is None else SimpleNamespace(id=7, holder_user_id=holder)
    deps.lock_repository.get_by_key.side_effect = lambda key: lock if key == "bot-1:original" else None
    deps.mcp_provider.collect_bot_active_mcps.return_value = [{"server_code": "calendar"}]
    deps.repository.replace_draft_call_type.return_value = DraftCallTypeMutationResult(previous_explicit_call_type=None, bot_call_type=McpCallType.CALLER, revision=3)
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {"success": True}
    return svc, deps


@pytest.mark.parametrize("holder,editable", [(None, True), ("new", True), ("other", False)])
def test_new_owner_context_observes_original_entity_lock(holder, editable):
    svc, deps = _handoff_service(holder)
    result = svc.get_context(bot_id="bot-1", actor_id="new", entity_id="original", stage=CallerIdentityStage.DRAFT)
    assert result.editable is editable
    deps.lock_repository.get_by_key.assert_called_once_with("bot-1:original")


@pytest.mark.asyncio
async def test_new_owner_can_change_mcp_under_existing_entity_lock():
    svc, deps = _handoff_service("new")
    result = await svc.update_mcp_call_type(bot_id="bot-1", actor_id="new", entity_id="original", server_code="calendar", call_type=McpCallType.CALLER, lock_epoch=7)
    assert result.call_type is McpCallType.CALLER
    mutation = deps.repository.replace_draft_call_type.call_args.kwargs
    assert mutation["lock_key"] == "bot-1:original"
    assert mutation["lock_holder_user_id"] == "new"
    assert mutation["modifier_id"] == "new"


@pytest.mark.asyncio
async def test_other_holder_cannot_be_bypassed_by_new_owner_key():
    svc, deps = _handoff_service("other")
    with pytest.raises(CallerLockEpochError):
        await svc.update_mcp_call_type(bot_id="bot-1", actor_id="new", entity_id="original", server_code="calendar", call_type=McpCallType.CALLER, lock_epoch=7)
    deps.repository.replace_draft_call_type.assert_not_called()
    deps.lock_repository.get_by_key.assert_called_once_with("bot-1:original")


@pytest.mark.asyncio
async def test_former_owner_is_not_authorized_by_stable_entity():
    svc, deps = _handoff_service("new")
    with pytest.raises(CallerIdentityPermissionError):
        await svc.update_mcp_call_type(bot_id="bot-1", actor_id="owner-1", entity_id="original", server_code="calendar", call_type=McpCallType.CALLER, lock_epoch=7)
    deps.repository.replace_draft_call_type.assert_not_called()


@pytest.mark.asyncio
async def test_iam_refresh_does_not_bypass_another_persons_entity_lock():
    svc, identity, auth, _, _, bindings, locks = iam_service(context=_context(owner_id="new"))
    auth.resolve_user_from_request.return_value = SimpleNamespace(staffId="new")
    locks.get_by_key.side_effect = lambda key: SimpleNamespace(holder_user_id="other") if key == "bot-1:entity-1" else None
    await svc.get_iam_token(iam_token="iam-test", auth_request=_request(), bot_id="bot-1", stage=CallerIdentityStage.DRAFT, publish_id=None, entity_id=None, is_test_exchange=False)
    locks.get_by_key.assert_called_once_with("bot-1:entity-1")
    assert all(call.args[0].target is RuntimeBindingTarget.CALLER_INSTANCE for call in bindings.resolve.call_args_list)
    assert bindings.resolve.call_count == 1


@pytest.mark.asyncio
async def test_failed_mcp_sync_compensates_under_the_same_entity_lock():
    from agentclaw.community.core.caller_identity.contracts import CallerMcpSyncError
    svc, deps = _handoff_service("new")
    deps.mcp_sync_service.sync_mcp_identity_to_agent_principal.return_value = {"success": False}
    with pytest.raises(CallerMcpSyncError):
        await svc.update_mcp_call_type(bot_id="bot-1", actor_id="new", entity_id="original", server_code="calendar", call_type=McpCallType.CALLER, lock_epoch=7)
    compensation = deps.repository.compensate_draft_call_type.call_args.kwargs
    assert compensation["lock_key"] == "bot-1:original"
    assert compensation["lock_holder_user_id"] == "new"
    assert compensation["lock_epoch"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("holder,allowed", [("new", True), ("other", False)])
async def test_acli_add_observes_the_same_entity_lock_after_handoff(holder, allowed):
    from tests.community.core.acli_clis.test_service import _World
    from tests.community.core.acli_clis.fakes import bot
    world = _World(record=bot(owner_id="new", entity_id="original"))
    keys = []

    def get_lock(key):
        keys.append(key)
        return SimpleNamespace(id=7, holder_user_id=holder) if key == "bot-1:original" else None

    world.service._locks = SimpleNamespace(get_by_key=get_lock)
    kwargs = dict(bot_id="bot-1", actor_id="new", entity_id="original", cli_code="yuque-cli", lock_epoch=7, run_async=True)
    if allowed:
        await world.service.add(**kwargs)
    else:
        with pytest.raises(CallerLockEpochError):
            await world.service.add(**kwargs)
    assert keys == ["bot-1:original"]
