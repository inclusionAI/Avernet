"""The Bot-scoped handoff from Manifest execution to its capability writers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


@dataclass(frozen=True, slots=True)
class CapabilityProjectionIntent:
    tenant: str
    env: str
    bot_id: str
    entity_id: str
    retired_mappings: tuple[PoolSkillMapping, ...]


_ACTIVE_INTENT: ContextVar[CapabilityProjectionIntent | None] = ContextVar(
    "capability_projection_intent", default=None,
)


@contextmanager
def defer_capability_projection(
    *, bot_id: str, entity_id: str, retired_mappings: Sequence[PoolSkillMapping],
) -> Iterator[None]:
    """Defer only the existing Bot's explicitly scoped Apply execution."""
    token = _ACTIVE_INTENT.set(CapabilityProjectionIntent(
        get_current_avernet_tenant(), get_current_env(), bot_id, entity_id,
        tuple(retired_mappings),
    ))
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
