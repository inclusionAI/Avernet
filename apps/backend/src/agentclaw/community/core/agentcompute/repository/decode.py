"""DB-string decoders for the agentcompute repositories.

Every persisted enum column comes back as a plain string; these restore the
typed domain enums with a fail-safe fallback per kind (an unknown legacy
value decodes to the family's terminal error state, never raises). Kept in
one module so the contract tests can pin the fallback table in one import.
"""
from __future__ import annotations

from agentclaw.community.core.agentcompute.domain import models as dm


def _decode_agent_status(val: str) -> dm.AgentStatus:
    try:
        return dm.AgentStatus(val)
    except ValueError:
        return dm.AgentStatus.FAILED


def _decode_bot_state(val: str | None) -> dm.BotState | None:
    if val is None:
        return None
    try:
        return dm.BotState(val)
    except ValueError:
        return None


def _decode_finish_reason(val: str | None) -> dm.FinishReason | None:
    if val is None:
        return None
    try:
        return dm.FinishReason(val)
    except ValueError:
        return dm.FinishReason.ERROR


def _decode_guard_kind(val: str) -> dm.GuardKind:
    try:
        return dm.GuardKind(val)
    except ValueError:
        return dm.GuardKind.KILL


def _decode_message_role(val: str) -> dm.MessageRole:
    try:
        return dm.MessageRole(val)
    except ValueError:
        return dm.MessageRole.SYSTEM


def _decode_run_status(val: str) -> dm.RunStatus:
    try:
        return dm.RunStatus(val)
    except ValueError:
        return dm.RunStatus.FAILED


def _decode_termination_reason(val: str | None) -> dm.TerminationReason | None:
    if val is None:
        return None
    try:
        return dm.TerminationReason(val)
    except ValueError:
        return dm.TerminationReason.ERROR