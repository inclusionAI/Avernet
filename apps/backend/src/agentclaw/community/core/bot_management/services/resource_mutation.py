"""Serialize legacy resource release with recycle's short admission lock.

A restart already holding the same lock lends its scope to stop_bot. This is
not a new lifecycle journal and never holds a lock during backup preparation.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps

from agentclaw.community.core.bot_dormant.recycle_state import assert_not_recycling
from agentclaw.community.utils.env_utils import get_current_env


held_resource_lock: ContextVar[tuple | None] = ContextVar(
    "held_resource_lock", default=None
)


def serialize_resource_release(method):
    @wraps(method)
    def guarded(self, bot_id, user_id, *args, **kwargs):
        from .bot_service import BotInvalidLifecycleStateError

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            return method(self, bot_id, user_id, *args, **kwargs)
        assert_not_recycling(bot)
        entity_id = bot.get("entity_id")
        # Recycle admission itself refuses records without this lock identity.
        if not entity_id:
            return method(self, bot_id, user_id, *args, **kwargs)
        key = (get_current_env(), entity_id, bot_id, user_id)
        if held_resource_lock.get() == key:
            return method(self, bot_id, user_id, *args, **kwargs)
        lock = self._try_acquire_restart_lock(*key)
        if lock is None:
            raise BotInvalidLifecycleStateError(bot_id=bot_id, current_status="BUSY")
        token = held_resource_lock.set(key)
        try:
            # The wrapped implementation re-reads and checks the journal under
            # the lock, before remote calls. stop leaves PENDING; delete removes
            # the live row, so recycle cannot admit after this lock is released.
            return method(self, bot_id, user_id, *args, **kwargs)
        finally:
            held_resource_lock.reset(token)
            self._restart_lock_repo.release(*key[:3], lock.lock_token)

    return guarded
