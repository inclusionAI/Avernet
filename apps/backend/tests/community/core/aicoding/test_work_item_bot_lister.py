"""Tests for the work-items Coding-bot lister."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.aicoding.work_item_bots import (
    RepositoryWorkItemsBotLister,
    is_coding_session_list_bot,
    WorkItemsBotTarget,
)


def _lister(bot_repository=None, collaborator_repository=None):
    return RepositoryWorkItemsBotLister(
        bot_repository=MagicMock() if bot_repository is None else bot_repository,
        collaborator_repository=MagicMock()
        if collaborator_repository is None
        else collaborator_repository,
    )


def test_coding_session_list_predicate_matches_the_frontend_coding_scope():
    assert is_coding_session_list_bot(
        {"active_engine": "aicoding", "template_type": "personalCoding"}
    )
    assert is_coding_session_list_bot(
        {
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
        }
    )
    assert is_coding_session_list_bot(
        {
            "active_engine": "claude_code",
            "template_type": "architect",
            "template_config": {"capabilities": {"aix": True}},
        }
    )
    assert is_coding_session_list_bot(
        {"active_engine": "claude_code", "template_type": "architect"}
    )
    assert is_coding_session_list_bot(
        {
            "active_engine": "claude_code",
            "template_type": "architect",
            "template_config": {"name": "architect"},
        }
    )
    assert not is_coding_session_list_bot(
        {"active_engine": "claude_code", "template_type": "normal"}
    )
    assert not is_coding_session_list_bot(
        {"active_engine": "claude_code", "template_type": "normalCC"}
    )
    assert not is_coding_session_list_bot(
        {
            "active_engine": "claude_code",
            "template_type": "architect",
            "template_config": {"capabilities": {}},
        }
    )


def test_owner_bots_are_enumerated_across_all_repository_pages():
    bot_repository = MagicMock()
    bot_repository.list_by_owner.side_effect = [
        (150, [{"active_engine": "aicoding", "bot_id": "bot-1"}] * 100),
        (150, [{"active_engine": "aicoding", "bot_id": "bot-2"}] * 50),
    ]
    collaborator_repository = MagicMock()
    collaborator_repository.list_by_user.return_value = []

    targets = _lister(bot_repository, collaborator_repository).list_bot_targets("u1")

    assert [target.bot_id for target in targets] == ["bot-1", "bot-2"]
    assert bot_repository.list_by_owner.call_count == 2
    collaborator_repository.list_by_user.assert_called_once_with(
        "u1", collaborator_repository.list_by_user.call_args[0][1]
    )


def test_owner_and_collaborator_bots_are_deduped_by_bot_and_owner_pair():
    bot_repository = MagicMock()
    bot_repository.list_by_owner.return_value = (
        1,
        [
            {
                "active_engine": "aicoding",
                "bot_id": "default",
                "bot_name": "My default",
            }
        ],
    )
    collaborator_repository = MagicMock()
    collaborator_repository.list_by_user.return_value = [
        SimpleNamespace(bot_id="default", owner_id="u1"),
        SimpleNamespace(bot_id="default", owner_id="other-owner"),
    ]
    bot_repository.get_by_id_and_owner.return_value = {
        "active_engine": "aicoding",
        "bot_id": "default",
        "bot_name": "Shared default",
    }

    targets = _lister(bot_repository, collaborator_repository).list_bot_targets("u1")

    assert targets == [
        WorkItemsBotTarget("default", "Shared default", "other-owner"),
        WorkItemsBotTarget("default", "My default", "u1"),
    ]
    bot_repository.get_by_id_and_owner.assert_called_once_with("default", "other-owner")
