"""Transport-neutral expert catalogue service contract."""

from typing import Any, Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.expert_market.models import CompiledExpert


@runtime_checkable
class ExpertCatalogServiceProtocol(Protocol):
    """Current-scope discovery; sources are selected by deployment, never caller input."""

    def search(
        self,
        *,
        tenant: str,
        keyword: str,
        category: str | None,
        sort: str,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def get(self, *, tenant: str, expert_id: str) -> dict[str, Any]: ...
    def categories(self, *, tenant: str) -> list[str]: ...


class ExpertCatalogRepositoryProtocol(Protocol):
    """Catalogue-owned persistence, with explicit tenant/environment/source scope."""

    def reconcile(
        self, scope, source_commit: str, valid: dict, errors: dict, seen: set[str]
    ) -> None: ...
    def search(
        self,
        scope,
        *,
        keyword: str,
        category: str | None,
        sort: str,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def get(self, scope, expert_id: str) -> dict[str, Any] | None: ...
    def categories(self, scope) -> list[str]: ...


class ExpertScannerProtocol(Protocol):
    """Existing GitSync supplies one verified commit to the expert reconciler."""

    def scan_git(self, bare_repo, *, source_commit: str, scope): ...


@runtime_checkable
class ExpertCreationServiceProtocol(Protocol):
    def compile(self, *, tenant: str, expert_id: str, engine: str) -> "CompiledExpert":
        """Bind the latest valid content to a controlled source and target engine."""
        ...
