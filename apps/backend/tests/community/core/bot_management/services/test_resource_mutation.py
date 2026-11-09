"""Release shares recycle admission's lock, with explicit restart reentrancy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
)
from agentclaw.community.core.bot_management.services.resource_mutation import (
    held_resource_lock,
    serialize_resource_release,
)
from agentclaw.community.utils.env_utils import get_current_env


class Service:
    def __init__(self):
        self.bot = {"bot_id": "b", "owner_id": "u", "entity_id": "u", "active_engine": "claude_code", "ext": {}}
        self._repository = SimpleNamespace(
            get_by_id_and_owner=Mock(side_effect=lambda *_: self.bot)
        )
        self._restart_lock_repo = Mock()
        self._try_acquire_restart_lock = Mock(
            return_value=SimpleNamespace(lock_token="token")
        )
        self.effect = Mock()

    @serialize_resource_release
    def release(self, bot_id, user_id):
        from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
            assert_not_recycling,
        )

        assert_not_recycling(self.bot)
        self.effect()


def test_release_serializes_and_releases_after_effect():
    service = Service()
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_called_once_with(
        get_current_env(), "u", "b", "u"
    )
    service._restart_lock_repo.release.assert_called_once_with(
        get_current_env(), "u", "b", "token"
    )
    assert held_resource_lock.get() is None


def test_recycle_committed_before_lock_is_rechecked():
    service = Service()

    def acquire(*_):
        service.bot["ext"]["recycle"] = {"phase": "QUEUED"}
        return SimpleNamespace(lock_token="token")

    service._try_acquire_restart_lock.side_effect = acquire
    with pytest.raises(BotInvalidLifecycleStateError):
        service.release("b", "u")
    service.effect.assert_not_called()
    service._restart_lock_repo.release.assert_called_once()


def test_existing_restart_scope_does_not_reacquire_or_release_lock():
    service = Service()
    token = held_resource_lock.set((get_current_env(), "u", "b", "u"))
    try:
        service.release("b", "u")
    finally:
        held_resource_lock.reset(token)
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_not_called()
    service._restart_lock_repo.release.assert_not_called()


def test_busy_release_has_no_side_effect():
    service = Service()
    service._try_acquire_restart_lock.return_value = None
    with pytest.raises(BotInvalidLifecycleStateError):
        service.release("b", "u")
    service.effect.assert_not_called()


def test_default_engine_release_does_not_acquire_coding_lock():
    service = Service()
    service.bot["active_engine"] = "openclaw"
    service.release("b", "u")
    service.effect.assert_called_once()
    service._try_acquire_restart_lock.assert_not_called()
    service._restart_lock_repo.release.assert_not_called()
