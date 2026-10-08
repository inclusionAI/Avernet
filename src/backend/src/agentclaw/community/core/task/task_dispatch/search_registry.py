"""Governed search-provider selection shared by centralized and Relay flows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError


DEFAULT_SEARCH_STRATEGY = "default"


class TaskSearchRegistry:
    """Resolve a frozen task search strategy to its discovery port.

    The registry owns no transport policy. Composition roots register concrete
    discovery ports under stable names; task runtime profiles only retain those
    names so retries and Relay hand-offs keep using the same provider.
    """

    def __init__(
        self,
        default_discover: Any = None,
        *,
        strategies: Mapping[str, Any] | None = None,
    ) -> None:
        registered: dict[str, Any] = {DEFAULT_SEARCH_STRATEGY: default_discover}
        for raw_name, discover in (strategies or {}).items():
            name = str(raw_name or "").strip()
            if not name:
                raise TaskStateError("search strategy name must be a non-empty string")
            registered[name] = discover
        self._strategies = registered

    def resolve(self, strategy: str = DEFAULT_SEARCH_STRATEGY) -> Any:
        name = str(strategy or DEFAULT_SEARCH_STRATEGY).strip()
        if name not in self._strategies:
            available = ", ".join(sorted(self._strategies))
            raise TaskStateError(
                f"unknown search_strategy={name!r}; registered strategies: {available}"
            )
        return self._strategies[name]

    def backend_name(self, strategy: str = DEFAULT_SEARCH_STRATEGY) -> str:
        discover = self.resolve(strategy)
        return type(discover).__name__ if discover is not None else "None"


def frozen_search_strategy(graph: Any) -> str:
    """Read the task-level search strategy frozen into a runtime graph."""
    runtime_profile = graph.extend_props.get("runtime_profile", {}) or {}
    return str(runtime_profile.get("search_strategy") or DEFAULT_SEARCH_STRATEGY)


def task_search_strategy(graph_service: Any, task_id: str | None) -> str:
    """Resolve a Relay task strategy while preserving legacy pre-graph search."""
    if not task_id:
        return DEFAULT_SEARCH_STRATEGY
    from agentclaw.community.core.task.domain.errors import TaskNotFoundError

    try:
        graph = graph_service.query_task_dashboard(task_id)
    except TaskNotFoundError:
        return DEFAULT_SEARCH_STRATEGY
    return frozen_search_strategy(graph)
