"""Bounded parallel execution for independent retrieval branches."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True, slots=True)
class RetrievalBranchResult(Generic[T]):
    """One branch outcome without hiding provider failures."""

    value: T | None = None
    error: BaseException | None = None


class RetrievalBranchRunner:
    """Execute two retrieval calls concurrently while preserving both outcomes."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        if max_workers < 2:
            raise ValueError("max_workers must be at least 2")
        self._owns_executor = executor is None
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="bcsfuse-retrieval",
        )

    def run(
        self,
        dense: Callable[[], T],
        keyword: Callable[[], U],
    ) -> tuple[RetrievalBranchResult[T], RetrievalBranchResult[U]]:
        """Run both branches and wait for both before returning either result."""
        dense_future = self._submit("dense", dense)
        keyword_future = self._submit("keyword", keyword)
        return (
            _result(dense_future),
            _result(keyword_future),
        )

    def _submit(self, label: str, operation: Callable[[], T]) -> Future[T]:
        del label
        return self._executor.submit(operation)

    def close(self) -> None:
        """Release an owned worker pool; externally supplied pools stay open."""
        if self._owns_executor:
            self._executor.shutdown(wait=True)


def _result(
    future: Future[T],
) -> RetrievalBranchResult[T]:
    try:
        return RetrievalBranchResult(value=future.result())
    except BaseException as error:  # noqa: BLE001 -- bounded branch result
        return RetrievalBranchResult(error=error)


__all__ = [
    "RetrievalBranchResult",
    "RetrievalBranchRunner",
]
