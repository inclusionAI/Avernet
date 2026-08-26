"""Create-combination policy for personal cloud and local Bots."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.bot_inventory.types import DeployMode
from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES

LOCAL_CAPABLE_ENGINES = frozenset({"openclaw", "claude_code"})
PERSONAL_CLOUD_CAPABLE_ENGINES = frozenset(SUPPORTED_ENGINE_TYPES)
SERVICE_CAPABLE_ENGINES = frozenset({"openclaw", "claude_code", "teclaw"})
APPLICATION_CODING_ENGINES = frozenset({"claude_code"})


@dataclass(frozen=True)
class ComboDecision:
    ok: bool
    reason: str | None = None


def assert_personal_cloud_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in PERSONAL_CLOUD_CAPABLE_ENGINES:
        return ComboDecision(False, f"unsupported engine: {engine}")
    if space_kind not in {"personal", "team"}:
        return ComboDecision(
            False, "personal cloud bot requires a valid business space"
        )
    return ComboDecision(True)


def assert_local_create(engine: str, space_kind: str) -> ComboDecision:
    if engine not in LOCAL_CAPABLE_ENGINES:
        return ComboDecision(False, f"local bot does not support engine: {engine}")
    if space_kind != "personal":
        return ComboDecision(False, "local bot is personal business-space only")
    return ComboDecision(True)


def assert_service_upgrade(engine: str) -> ComboDecision:
    if engine not in SERVICE_CAPABLE_ENGINES:
        return ComboDecision(False, f"engine cannot be serviced: {engine}")
    return ComboDecision(True)


def assert_application_coding_create(
    *,
    engine: str,
    bot_type: str,
    space_kind: str,
    deployment_mode: DeployMode,
) -> ComboDecision:
    """Application-coding create combo: cloud + personal + non-service + claude_code.

    ``deployment_mode`` and ``space_kind`` are explicit rather than inferred from
    the calling endpoint, so the rule is self-contained and unit-testable.
    ``claude_code`` is the only external engine value: the runtime routing to the
    ``aicoding`` adapter is an internal concern, not an alternative engine.

    The production implementation of this gate is
    ``bot_management/engines/aicoding/strategy.py``
    ``AicodingProvisioningStrategy.prepare_create`` (same order, same
    messages). This copy has no production caller today — keep the two in
    sync, or single-source them once bot_inventory may depend on
    bot_management.
    """
    if deployment_mode is not DeployMode.CLOUD:
        return ComboDecision(False, "application coding is cloud-only")
    if engine not in APPLICATION_CODING_ENGINES:
        return ComboDecision(
            False, f"application coding does not support engine: {engine}"
        )
    if bot_type != "personal":
        return ComboDecision(False, "application coding bot must be personal")
    if space_kind != "personal":
        return ComboDecision(False, "application coding is personal-space only")
    return ComboDecision(True)
