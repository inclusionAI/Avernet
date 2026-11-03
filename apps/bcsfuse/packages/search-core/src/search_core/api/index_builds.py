"""Index rebuild management contract, independent of transport and source type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from search_core.config.index_builds import IndexBuildConfig


class IndexBuildConflict(ValueError):
    """An immutable build identity or source publication precondition conflicts."""


@dataclass(frozen=True)
class IndexBuildRecord:
    config: IndexBuildConfig
    status: str
    completed_count: int
    total_count: int
    error_code: str


class IndexBuildManagement(Protocol):
    def create(self, build_id: str, model: str, dimension: int) -> IndexBuildRecord: ...
    def status(self, build_id: str) -> IndexBuildRecord: ...
