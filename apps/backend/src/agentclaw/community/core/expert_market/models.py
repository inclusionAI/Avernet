"""Values passed across the expert catalogue boundary."""

from __future__ import annotations

from dataclasses import dataclass
from agentclaw.community.core.bot_management.expert_origin import ExpertOrigin


@dataclass(frozen=True)
class ExpertScope:
    tenant: str
    env: str
    source_repo_id: str


@dataclass(frozen=True)
class ScanResult:
    valid: int
    invalid: int
    disabled: int


@dataclass(frozen=True)
class CompiledExpert:
    document: str
    origin: ExpertOrigin


class ExpertNotFoundError(LookupError):
    """The expert is not discoverable in the caller's scope."""


class ExpertCreationError(ValueError):
    """An expert cannot be adapted safely for the requested creation."""
