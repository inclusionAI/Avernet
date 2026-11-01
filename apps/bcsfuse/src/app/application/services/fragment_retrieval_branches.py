"""Shared execution helper for independent dense and lexical retrieval."""

from __future__ import annotations

from typing import Callable

from app.application.services.retrieval_branch_runner import (
    RetrievalBranchResult,
    RetrievalBranchRunner,
)


def run_retrieval_branches(
    dense: Callable[[], object],
    keyword: Callable[[], object],
    runner: RetrievalBranchRunner | None,
) -> tuple[
    RetrievalBranchResult[object], RetrievalBranchResult[object]
]:
    """Run both retrieval branches, preserving either branch's failure."""

    if runner is not None:
        return runner.run(dense, keyword)
    try:
        dense_result = RetrievalBranchResult(value=dense())
    except BaseException as error:  # noqa: BLE001 -- branch result
        dense_result = RetrievalBranchResult(error=error)
    try:
        keyword_result = RetrievalBranchResult(value=keyword())
    except BaseException as error:  # noqa: BLE001 -- branch result
        keyword_result = RetrievalBranchResult(error=error)
    return dense_result, keyword_result


__all__ = ["run_retrieval_branches"]
