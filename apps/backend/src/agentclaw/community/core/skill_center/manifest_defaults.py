"""Immutable, Bot-scoped Default protection facts for one Manifest plan.

These are control-plane facts, not a runtime or whole-Apply snapshot. Commands
re-read them under their existing transaction locks before changing provenance.
"""

from collections.abc import Callable
from typing import ContextManager
from dataclasses import dataclass


@dataclass(frozen=True)
class ManifestDefaults:
    skill_ids: frozenset[str]
    skill_names: frozenset[str]
    mcp_codes: frozenset[str]
    platform_codes: frozenset[str]
    fingerprint: str
    check_skill_sources: bool = True


class ManifestDefaultsConflictError(ValueError):
    """The protection premise changed or cannot be established safely."""


# The upload road enters this only around physical publication/restore.

PackageWriteGuard = Callable[[], ContextManager[None]]


class ManifestPackageWriteCommittedError(RuntimeError):
    """A protected package write could not be fully compensated after failure."""
