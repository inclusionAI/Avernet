"""Request-local handoff from Manifest Apply to its capability writers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


@dataclass(slots=True)
class CapabilityProjectionIntent:
    bot_id: str
    entity_id: str
    tenant: str = field(default_factory=get_current_avernet_tenant)
    env: str = field(default_factory=get_current_env)
    _committed: list[ProjectionScope] = field(default_factory=list, init=False)

    def record(self, scope: ProjectionScope) -> None:
        self._committed.append(scope)

    def scope(self) -> ProjectionScope:
        # Each writer appends after its commit. Apply reads the scopes after
        # the capability writer and every in-flight package have returned.
        return ProjectionScope(
            skills=any(change.skills for change in self._committed),
            mcp=any(change.mcp for change in self._committed),
            claimed_mcp=frozenset().union(*(change.claimed_mcp for change in self._committed)),
            updated_mcp=frozenset().union(*(change.updated_mcp for change in self._committed)),
            released_mcp=frozenset().union(*(change.released_mcp for change in self._committed)),
            claim_all_mcp=any(change.claim_all_mcp for change in self._committed),
            passport=any(change.passport for change in self._committed),
        )


_ACTIVE_INTENT: ContextVar[CapabilityProjectionIntent | None] = ContextVar(
    "capability_projection_intent", default=None,
)


@contextmanager
def defer_capability_projection(intent: CapabilityProjectionIntent) -> Iterator[None]:
    token = _ACTIVE_INTENT.set(intent)
    try:
        yield
    finally:
        _ACTIVE_INTENT.reset(token)


def capability_projection_intent(
    *, bot_id: str, entity_id: str,
) -> CapabilityProjectionIntent | None:
    intent = _ACTIVE_INTENT.get()
    if intent is not None and (intent.tenant, intent.env, intent.bot_id, intent.entity_id) == (
        get_current_avernet_tenant(), get_current_env(), bot_id, entity_id,
    ):
        return intent
    return None
