"""Typed identifiers for the agentswarm domain (zero dependencies)."""
from __future__ import annotations

from typing import NewType

RunId = NewType("RunId", str)
AgentId = NewType("AgentId", str)
TenantId = NewType("TenantId", str)
JobId = NewType("JobId", str)
MessageId = NewType("MessageId", str)
ShardId = NewType("ShardId", str)
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