"""Index rebuild management contract, independent of transport and source type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from search_core.api.cards import UNSET_MAPPING, UnsetMapping

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
    mapping_warnings: tuple[dict, ...] = ()


class IndexBuildManagement(Protocol):
    def create(
        self,
        build_id: str,
        model: str,
        dimension: int,
        *,
        filter_fields: tuple[str, ...] | None = None,
    ) -> IndexBuildRecord:
        """Freeze code-selected conversion and filters. None means stored defaults
        for retries, or the application's defaults for a new version.
        """
        ...

    def status(self, build_id: str) -> IndexBuildRecord: ...


class MappedIndexBuildManagement(IndexBuildManagement, Protocol):
    """Opt-in creation with immutable mapping rules and optional sample validation."""

    def create(
        self,
        build_id: str,
        model: str,
        dimension: int,
        *,
        filter_fields: tuple[str, ...] | None = None,
        document_mapping: dict | None | UnsetMapping = UNSET_MAPPING,
        validation_card_ids: tuple[str, ...] = (),
    ) -> IndexBuildRecord: ...
