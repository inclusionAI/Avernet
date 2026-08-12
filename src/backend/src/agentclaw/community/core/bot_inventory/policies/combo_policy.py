"""Create-combination policy for personal cloud and local Bots."""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES

LOCAL_CAPABLE_ENGINES = frozenset({"openclaw", "claude_code"})
PERSONAL_CLOUD_CAPABLE_ENGINES = frozenset(SUPPORTED_ENGINE_TYPES)


@dataclass(frozen=True)
class ComboDecision:
    ok: bool
    reason: str | None = None


def assert_personal_cloud_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in PERSONAL_CLOUD_CAPABLE_ENGINES:
        return ComboDecision(False, f"unsupported engine: {engine}")
    if space_kind not in {"personal", "team"}:
        return ComboDecision(False, "personal cloud bot requires a valid business space")
    return ComboDecision(True)


def assert_local_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in LOCAL_CAPABLE_ENGINES:
        return ComboDecision(False, f"local bot does not support engine: {engine}")
    if space_kind != "personal":
        return ComboDecision(False, "local bot is personal business-space only")
    return ComboDecision(True)
