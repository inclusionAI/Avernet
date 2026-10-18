"""Typed identifiers for the agentswarm domain (zero dependencies)."""
from __future__ import annotations

import hashlib
from typing import NewType

RunId = NewType("RunId", str)
AgentId = NewType("AgentId", str)
TenantId = NewType("TenantId", str)
DispatchId = NewType("DispatchId", str)
MessageId = NewType("MessageId", str)
ResultId = NewType("ResultId", str)
RoundId = NewType("RoundId", str)
EventId = NewType("EventId", str)
ArtifactKey = NewType("ArtifactKey", str)
ModelId = NewType("ModelId", str)
# BotId / IdempotencyKey are BotPlatform seam vocabulary — defined in
# plugin_api.agentswarm.bot.protocol and re-exported here.
from agentclaw.community.plugin_api.agentswarm.bot import (
    BotId, IdempotencyKey,
)
SecretRef = NewType("SecretRef", str)
EpochMillis = NewType("EpochMillis", int)


def spawn_digest(run_id: str, agent_id: str, attempt: int,
                 digits: int = 16) -> str:
    """Hex SHA-1 prefix of the canonical per-attempt spawn key (D-J).

    The spawn idempotency key is ``f"{run_id}:{agent_id}:{attempt}"`` —
    attempt enters the digest, so a same-slice retry mints a fresh persona
    and a cross-slice replay of the same attempt number re-derives the
    crashed slice's identity (the adopt leg's input). This is the ONE
    canonical derivation: the bot platform's persona id
    (``agent_swarm_{sha1(key)[:16]}``), its display name (``{spec.name}-{sha1(key)[:8]}``),
    and the llm-kind bare executor digest all come from this helper, so
    runtime, plugins, and the deterministic-derivation cleanup belt can
    never drift apart. Host's create/adopt code stays untouched — it
    digests whatever key it is given; callers build that key from this
    same triple.
    """
    key = f"{run_id}:{agent_id}:{attempt}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:digits]