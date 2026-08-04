"""Conformance: the api-layer service-api Protocols mirror the concrete core
implementations' public surface (mirrors the bot_management
``test_service_api_conformance`` contract).

``api.task.service_api.{TaskServiceProtocol,TaskSchedulerProtocol}`` are the
contracts the HTTP router depends on (``Injected(...)``); DI binds them to the
core concrete services. Core must NOT import the api Protocols — its concrete
services satisfy them *structurally*. This test enforces that every method on
the api Protocol exists on the core concrete class so a missing/renamed method
fails CI rather than only at endpoint call time (the four-layer
``adapters → api → core`` seam stays sound).
"""
from __future__ import annotations

import ast
import inspect

from agentclaw.community.api.task.service_api import (
    TaskSchedulerProtocol,
    TaskServiceProtocol,
)
from agentclaw.community.core.task.services import (
    TaskScheduler as CoreTaskScheduler,
)
from agentclaw.community.core.task.services import (
    TaskService as CoreTaskService,
)


def _protocol_methods(protocol_cls: type) -> set[str]:
    """Public methods declared on a Protocol (exclude dunders + module-level)."""
    return {
        name
        for name, member in vars(protocol_cls).items()
        if not name.startswith("_") and callable(member)
    }


def _assert_conforms(core_cls: type, protocol_cls: type) -> None:
    proto_methods = _protocol_methods(protocol_cls)
    missing = {m for m in proto_methods if not hasattr(core_cls, m)}
    assert not missing, (
        f"{core_cls.__name__} does not conform to {protocol_cls.__name__}: "
        f"missing {sorted(missing)}"
    )
    # Every Protocol method must resolve to a callable on the concrete class.
    for m in proto_methods:
        assert callable(getattr(core_cls, m)), (
            f"{core_cls.__name__}.{m} is not callable (Protocol {protocol_cls.__name__})"
        )


def test_task_service_conforms_to_api_protocol():
    """Core TaskService structurally satisfies api.task.TaskServiceProtocol."""
    _assert_conforms(CoreTaskService, TaskServiceProtocol)


def test_task_scheduler_conforms_to_api_protocol():
    """Core TaskScheduler structurally satisfies api.task.TaskSchedulerProtocol."""
    _assert_conforms(CoreTaskScheduler, TaskSchedulerProtocol)


def test_api_protocols_are_runtime_checkable_protocols():
    """The api service-api Protocols are runtime_checkable (so ``Injected(...)``
    + isinstance checks resolve in DI). ``@runtime_checkable`` sets
    ``_is_runtime_protocol``; the structural-contract behavior is separately
    proven by the conformance tests above."""
    assert getattr(TaskServiceProtocol, "_is_runtime_protocol", False)
    assert getattr(TaskSchedulerProtocol, "_is_runtime_protocol", False)


def test_api_protocol_surface_is_stable():
    """Guard the api surface the router depends on — adding/removing a method
    here must be deliberate (router + DI + conformance all reference it)."""
    assert _protocol_methods(TaskServiceProtocol) == {
        "get",
        "list_by_user",
        "progress",
        "create",
        "clarify",
        "on_event",
        "claim_node",
        "release_node",
        "history",
        "latest_seq",
    }
    assert _protocol_methods(TaskSchedulerProtocol) == {"start", "tick", "on_event"}


def _import_targets(module_obj) -> list[str]:
    """AST-extract every ``from <x> import ...`` target module in a module's
    source (docstring-safe — substring checks would false-positive on
    docstring mentions of ``agentclaw.community.core.*``)."""
    src = inspect.getsource(module_obj)
    tree = ast.parse(src)
    return [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]


def test_api_protocol_imports_do_not_pull_core():
    """The api service_api module must import NO core code at all (``adapters →
    api``; api↔core decoupled via DI — mirrors api/bot_service.BotServiceProtocol
    which uses ``*args/**kwargs -> Any`` precisely to avoid core imports)."""
    import agentclaw.community.api.task.service_api as api_mod

    offending = [t for t in _import_targets(api_mod) if t.startswith("agentclaw.community.core")]
    assert not offending, (
        "api/task/service_api.py must not import core (api↔core decoupled via DI, "
        f"not direct imports); offending core imports: {offending}. "
        "Use *args/**kwargs -> Any like api/bot_service.py"
    )


def test_api_task_package_does_not_reexport_core():
    """The api/task package (__init__) must NOT re-export core Protocols/DTOs —
    only the loose api service Protocols. Port Protocols + core-internal
    TaskService/TaskScheduler live in core.task.protocols, imported from there
    by core/plugins/DI (not via api)."""
    import agentclaw.community.api.task as api_pkg

    offending = [t for t in _import_targets(api_pkg) if t.startswith("agentclaw.community.core")]
    assert not offending, (
        "api/task/__init__.py must not import/re-export core (api↔core decoupled); "
        f"offending core imports: {offending}"
    )
