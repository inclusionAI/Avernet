"""Consumer contract: live membership + trusted sync precede every provider call."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.space_knowledge.service import SpaceKnowledgeService
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    KnowledgeBaseState,
    SpaceKnowledgeError,
    SyncedSpace,
)


def setup_service():
    space = SimpleNamespace(id=7, env="test")
    access = Mock()
    access.require_space_member.return_value = (space, SimpleNamespace(role="MEMBER"))
    sync = Mock()
    sync.get_context.return_value = sync.ensure_synced.return_value = SyncedSpace(
        "7", "test", 12
    )
    provider = Mock()
    state = KnowledgeBaseState(
        "kb-one", "tenant-one", "Knowledge", "", "creating", "001234", "001234"
    )
    result = KnowledgeBaseResult(state, True)
    provider.create.return_value = provider.get.return_value = result
    provider.list_knowledge_bases.return_value = KnowledgeBasePage([state])
    return SpaceKnowledgeService(access, sync, provider), access, sync, provider, result


@pytest.mark.parametrize("operation", ["create", "get", "list_knowledge_bases"])
def test_member_calls_provider_only_after_sync(operation):
    service, access, sync, provider, expected = setup_service()
    kwargs = {"space_id": 7, "actor_id": "001234"}
    kwargs.update(
        {"request_id": "stable", "name": "Knowledge", "description": ""}
        if operation == "create"
        else {"kb_id": "kb-one"}
        if operation == "get"
        else {}
    )
    assert getattr(service, operation)(**kwargs) == (
        KnowledgeBasePage([expected.state])
        if operation == "list_knowledge_bases"
        else expected
    )
    method = sync.ensure_synced if operation == "create" else sync.get_context
    method.assert_called_once_with(space_id=7, environment="test")
    if operation != "create":
        sync.ensure_synced.assert_not_called()
    assert access.require_space_member.call_count == 2
    assert getattr(provider, operation).call_args.kwargs["actor_id"] == "001234"
    assert getattr(provider, operation).call_args.kwargs["space"] == SyncedSpace(
        "7", "test", 12
    )


@pytest.mark.parametrize(
    "synced",
    [
        SyncedSpace("8", "test", 12),
        SyncedSpace("7", "prod", 12),
        SyncedSpace("7", "test", 0),
        SyncedSpace("7", "test", True),
    ],
)
def test_invalid_sync_is_not_a_browser_revision_or_other_space(synced):
    service, _, sync, provider, _ = setup_service()
    sync.ensure_synced.return_value = synced
    with pytest.raises(SpaceKnowledgeError):
        service.create(
            space_id=7,
            actor_id="001234",
            request_id="stable",
            name="Knowledge",
            description="",
        )
    provider.create.assert_not_called()


@pytest.mark.parametrize("second_check", [False, True])
def test_revoked_member_cannot_use_provider(second_check):
    service, access, _, provider, _ = setup_service()
    access.require_space_member.side_effect = (
        [(SimpleNamespace(id=7, env="test"), None), SpaceAccessDeniedError()]
        if second_check
        else SpaceAccessDeniedError()
    )
    with pytest.raises(SpaceAccessDeniedError):
        service.get(space_id=7, actor_id="001234", kb_id="kb-one")
    provider.get.assert_not_called()


def test_missing_producer_fails_closed():
    from agentclaw.community.plugins.unavailable.space_knowledge import (
        UnavailableSpaceKnowledge,
    )

    service, _, sync, provider, _ = setup_service()
    sync.get_context.side_effect = UnavailableSpaceKnowledge().get_context
    with pytest.raises(SpaceKnowledgeError, match="SPACE_SYNC_UNAVAILABLE"):
        service.get(space_id=7, actor_id="001234", kb_id="kb-one")
    provider.get.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "get", "list_knowledge_bases"])
@pytest.mark.parametrize("code", ["SPACE_NOT_SYNCED", "SPACE_SYNC_UNAVAILABLE"])
def test_unacknowledged_sync_never_reaches_provider(operation, code):
    service, _, sync, provider, _ = setup_service()
    sync.get_context.side_effect = sync.ensure_synced.side_effect = SpaceKnowledgeError(
        code
    )
    kwargs = {"space_id": 7, "actor_id": "001234"}
    kwargs.update(
        {"request_id": "stable", "name": "Knowledge", "description": ""}
        if operation == "create"
        else {"kb_id": "kb-one"}
        if operation == "get"
        else {}
    )
    with pytest.raises(SpaceKnowledgeError, match=f"^{code}$"):
        getattr(service, operation)(**kwargs)
    getattr(provider, operation).assert_not_called()


def test_never_used_space_reads_do_not_activate_or_call_provider():
    service, _, sync, provider, _ = setup_service()
    sync.get_context.return_value = None
    assert service.list_knowledge_bases(
        space_id=7, actor_id="001234"
    ) == KnowledgeBasePage([])
    with pytest.raises(SpaceKnowledgeError, match="RESOURCE_NOT_FOUND"):
        service.get(space_id=7, actor_id="001234", kb_id="kb")
    sync.ensure_synced.assert_not_called()
    provider.get.assert_not_called()
    provider.list_knowledge_bases.assert_not_called()
