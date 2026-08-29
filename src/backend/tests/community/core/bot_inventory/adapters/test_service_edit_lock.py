from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_collaborator.models import (
    BotCollabLockRecord,
    CollaboratorRecord,
)
from agentclaw.community.core.bot_inventory.adapters.service_edit_lock import (
    ServiceEditLockView,
)


@pytest.mark.unit
def test_states_for_bots_reads_collaborators_and_locks_once(monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "dev")
    collaborator_repo = MagicMock()
    collaborator_repo.list_by_bot_owner_pairs.return_value = [
        CollaboratorRecord(
            bot_pk=1,
            bot_id="service-1",
            owner_id="owner-1",
            user_id="editor-1",
            user_name="Editor One",
            operator_id="owner-1",
        ),
        CollaboratorRecord(
            bot_pk=2,
            bot_id="service-2",
            owner_id="owner-2",
            user_id="editor-2",
            user_name="Editor Two",
            operator_id="owner-2",
        ),
    ]
    lock_repo = MagicMock()
    lock_repo.list_by_keys.return_value = [
        BotCollabLockRecord(
            lock_key="service-1:owner-1",
            holder_user_id="editor-1",
        ),
        BotCollabLockRecord(
            lock_key="service-2:owner-2",
            holder_user_id="owner-2",
        ),
    ]
    view = ServiceEditLockView(collaborator_repo, lock_repo)

    states = view.states_for_bots(
        bots=[
            {
                "bot_id": "service-1",
                "owner_id": "owner-1",
                "owner_name": "Owner One",
            },
            {
                "bot_id": "service-2",
                "owner_id": "owner-2",
                "owner_name": "Owner Two",
            },
        ]
    )

    collaborator_repo.list_by_bot_owner_pairs.assert_called_once_with(
        [("service-1", "owner-1"), ("service-2", "owner-2")], "dev"
    )
    lock_repo.list_by_keys.assert_called_once_with(
        ["service-1:owner-1", "service-2:owner-2"]
    )
    assert states[("service-1", "owner-1")].holder_name == "Editor One"
    assert states[("service-1", "owner-1")].is_owner_holder is False
    assert states[("service-2", "owner-2")].holder_name == "Owner Two"
    assert states[("service-2", "owner-2")].is_owner_holder is True


@pytest.mark.unit
def test_states_for_bots_ignores_stale_lock_without_collaborators() -> None:
    collaborator_repo = MagicMock()
    collaborator_repo.list_by_bot_owner_pairs.return_value = []
    lock_repo = MagicMock()
    lock_repo.list_by_keys.return_value = [
        BotCollabLockRecord(
            lock_key="service-1:owner-1",
            holder_user_id="owner-1",
        )
    ]
    view = ServiceEditLockView(collaborator_repo, lock_repo)

    state = view.states_for_bots(
        bots=[{"bot_id": "service-1", "owner_id": "owner-1"}]
    )[("service-1", "owner-1")]

    assert state.has_collaborators is False
    assert state.locked is False
    assert state.holder_user_id is None
