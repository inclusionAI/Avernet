"""Unit tests for FriendAuthSyncService (mock all 3 Protocol deps)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_public.services.friend_auth_sync_service import (
    AgentCodeUnavailableError,
    AuthRelationshipSyncError,
    BotNotFoundError,
    FriendAuthSyncService,
)


def _bot_record(owner_name="owner-1", agent_code="ac-123"):
    return {
        "bot_id": "bot-1",
        "owner_id": "85020",
        "owner_name": owner_name,
        "ext": {"passport": {"agent_code": agent_code}},
    }


_UNSET = object()


def _svc(bot=_UNSET, agent_code="ac-123", create_result=None, query_result=None):
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = _bot_record(agent_code=agent_code) if bot is _UNSET else bot
    passport = MagicMock()
    auth_rel = MagicMock()
    auth_rel.create_relationship.return_value = create_result
    auth_rel.delete_relationship.return_value = True
    auth_rel.query_relationships.return_value = query_result if query_result is not None else []
    return FriendAuthSyncService(
        bot_repo=bot_repo, passport_plugin=passport, auth_relationship_plugin=auth_rel
    ), bot_repo, auth_rel


def test_sync_grant_created_returns_auth_id():
    svc, _, auth_rel = _svc(create_result={"auth_id": 42})
    out = svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")
    assert out == {"synced": True, "reason": "created", "auth_id": 42}
    auth_rel.create_relationship.assert_called_once()
    kwargs = auth_rel.create_relationship.call_args.kwargs
    assert kwargs["work_no"] == "88123"
    assert kwargs["agent_code"] == "ac-123"
    assert kwargs["operator_work_no"] == "85020"
    assert kwargs["operator_name"] == "owner-1"
    assert kwargs["description"] == "Human-bot friend authorized by BCS edge grant"


def test_sync_grant_already_exists_is_idempotent():
    svc, _, auth_rel = _svc(create_result={"auth_id": None, "already_exists": True})
    out = svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")
    assert out == {"synced": True, "reason": "already_exists", "auth_id": None}


def test_sync_grant_none_result_raises_sync_error():
    svc, _, _ = _svc(create_result=None)
    with pytest.raises(AuthRelationshipSyncError):
        svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")


def test_sync_revoke_deletes_each_matched_auth_id():
    svc, _, auth_rel = _svc(query_result=[{"auth_id": 7}, {"authId": 9}, {"auth_id": None}])
    out = svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="revoke")
    assert out == {"synced": True, "reason": "deleted", "auth_id": None}
    assert auth_rel.delete_relationship.call_count == 2
    auth_rel.delete_relationship.assert_any_call(7)
    auth_rel.delete_relationship.assert_any_call(9)


def test_sync_revoke_no_match_is_idempotent_not_found():
    svc, _, auth_rel = _svc(query_result=[])
    out = svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="revoke")
    assert out == {"synced": True, "reason": "not_found", "auth_id": None}
    auth_rel.delete_relationship.assert_not_called()


def test_sync_strips_human_prefix_from_work_no():
    svc, _, auth_rel = _svc(create_result={"auth_id": 1})
    svc.sync(bot_id="bot-1", owner_work_no="human_85020", human_work_no="human_88123", action="grant")
    kwargs = auth_rel.create_relationship.call_args.kwargs
    assert kwargs["work_no"] == "88123"
    assert kwargs["operator_work_no"] == "85020"


def test_sync_agent_code_fallback_queries_passport_when_ext_missing():
    bot = {"bot_id": "bot-1", "owner_id": "85020", "owner_name": "owner-1", "ext": {}}
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = bot
    passport = MagicMock()
    passport.query_agent_passport.return_value = {"agent_code": "ac-fb"}
    auth_rel = MagicMock()
    auth_rel.create_relationship.return_value = {"auth_id": 5}
    svc = FriendAuthSyncService(
        bot_repo=bot_repo, passport_plugin=passport, auth_relationship_plugin=auth_rel
    )
    out = svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")
    assert out["reason"] == "created"
    assert auth_rel.create_relationship.call_args.kwargs["agent_code"] == "ac-fb"


def test_sync_raises_bot_not_found_when_repo_returns_none():
    svc, _, _ = _svc(bot=None)
    with pytest.raises(BotNotFoundError):
        svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")


def test_sync_raises_agent_code_unavailable_when_neither_ext_nor_query_resolve():
    bot = {"bot_id": "bot-1", "owner_id": "85020", "owner_name": "owner-1", "ext": {}}
    bot_repo = MagicMock()
    bot_repo.get_by_id_and_owner.return_value = bot
    passport = MagicMock()
    passport.query_agent_passport.return_value = None
    auth_rel = MagicMock()
    svc = FriendAuthSyncService(
        bot_repo=bot_repo, passport_plugin=passport, auth_relationship_plugin=auth_rel
    )
    with pytest.raises(AgentCodeUnavailableError):
        svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="grant")


def test_sync_revoke_failed_delete_raises_sync_error():
    svc, _, auth_rel = _svc(query_result=[{"auth_id": 7}])
    auth_rel.delete_relationship.return_value = False
    with pytest.raises(AuthRelationshipSyncError):
        svc.sync(bot_id="bot-1", owner_work_no="85020", human_work_no="88123", action="revoke")
