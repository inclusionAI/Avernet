"""Host-provided business source and durable build repository contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from search_core.api.index_builds import IndexBuildRecord

if TYPE_CHECKING:
    from search_core.config.index_builds import IndexBuildConfig
    from search_core.plugin_api.embedding import EmbeddingProvider


class IndexBuildRepository(Protocol):
    def create(
        self, config: IndexBuildConfig, *, enqueue: bool = True
    ) -> IndexBuildRecord:
        """Freeze configuration; enqueue=False only registers it as configured.

        Registration never resets existing task state. Explicit enqueue schedules
        configured/failed records; pending/running/completed records are unchanged.
        Configuration conflicts and persistence failures must propagate.
        """
        ...

    def get(self, build_id: str) -> IndexBuildRecord | None: ...
    def candidates(self) -> list[IndexBuildRecord]: ...
    def claim(self, build_id: str, token: str, seconds: float) -> bool: ...
    def progress(
        self, build_id: str, token: str, done: int, total: int, seconds: float
    ) -> bool: ...
    def finish(
        self, build_id: str, token: str, status: str, done: int, total: int, error: str
    ) -> bool: ...


class IndexBuildSource(Protocol):
    """Source IDs/revisions/deletion flags include tombstones; ordering is stable.

    rebuild publishes an atomic document group only if the observed source and
    execution ownership are still current. A competing producer/source change
    raises IndexBuildConflict; other failures must propagate.
    """

    def page(
        self, after: str, limit: int
    ) -> list[tuple[str, int, bool]] | list[tuple[str, int]]:
        """Return (source_id, revision, deleted); deleted rows are processed but not counted.

        Legacy (source_id, revision) rows remain supported and count every row,
        as before. Hosts must provide the deletion flag to exclude tombstones.
        """
        ...

    def prepared_revision(self, index_key: str, source_id: str) -> int | None: ...
    def rebuild(
        self,
        source_id: str,
        config: IndexBuildConfig,
        model: EmbeddingProvider,
        lease_seconds: float,
    ) -> None: ...
