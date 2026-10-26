"""Values passed across the expert catalogue boundary."""

from __future__ import annotations

from dataclasses import dataclass


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


class ExpertNotFoundError(LookupError):
    """The expert is not discoverable in the caller's scope."""


class ExpertCreationError(ValueError):
    """An expert cannot be adapted safely for the requested creation."""
