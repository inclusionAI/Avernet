"""Dispatch engine-owned release policy without a shared recycle state machine.

A neutral context lends the caller's existing restart lock to nested release.
Default engines add no lock or recycle journal policy.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps


held_resource_lock: ContextVar[tuple | None] = ContextVar(
    "held_resource_lock", default=None
)


def serialize_resource_release(method):
    @wraps(method)
    def guarded(self, bot_id, user_id, *args, **kwargs):
        from agentclaw.community.core.bot_management.engines.registry import (
            resolve_restart_strategy,
        )

        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            return method(self, bot_id, user_id, *args, **kwargs)
        ctx, strategy = resolve_restart_strategy(bot)
        return strategy.execute_resource_release(
            ctx,
            bot,
            lambda: method(self, bot_id, user_id, *args, **kwargs),
            lifecycle=self,
        )

    return guarded
