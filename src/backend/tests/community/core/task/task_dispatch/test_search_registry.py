from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.task_dispatch.search_registry import (
    TaskSearchRegistry,
)


class _Discover:
    pass


def test_registry_resolves_default_named_and_reports_backend_name():
    default = _Discover()
    treatment = object()
    registry = TaskSearchRegistry(
        default, strategies={"catalog-v2": treatment, "default": treatment}
    )

    assert registry.resolve() is treatment
    assert registry.resolve("catalog-v2") is treatment
    assert registry.backend_name("catalog-v2") == "object"


def test_registry_rejects_empty_and_unknown_strategy_names():
    with pytest.raises(TaskStateError, match="non-empty string"):
        TaskSearchRegistry(strategies={" ": object()})

    registry = TaskSearchRegistry(_Discover())
    with pytest.raises(TaskStateError, match="unknown search_strategy='missing'"):
        registry.resolve("missing")


def test_task_search_compatibility_properties_use_default_provider():
    from agentclaw.community.core.task.task_dispatch.search import TaskSearch

    search = TaskSearch(_Discover())

    assert search.available is True
    assert search.backend_name == "_Discover"
