"""Service API Protocols for the task module — the adapter-facing service api.

Mirrors ``api/bot_service.py: BotServiceProtocol``: method signatures use
``*args, **kwargs -> Any`` so this module imports **no** core code. The api
layer must not depend on core (``adapters → api``, api↔core decoupled via DI);
the concrete core services
(:class:`agentclaw.community.core.task.services.TaskService` /
``TaskScheduler``) structurally satisfy these Protocols and DI binds the api
Protocols to them. Conformance (every method name exists on the core concrete)
is enforced by
``tests/community/architecture/test_task_service_api_conformance.py``.

The Port Protocols (Discover/Decomposer/Driver/Execution/BbsCollab/Panel) +
the core-internal ``TaskService``/``TaskScheduler`` Protocols live in
``core/task/protocols.py`` and are consumed by core/plugins/DI directly — they
are NOT re-exported here (api must not import core).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskServiceProtocol(Protocol):
    """Service API for the task authority — surface used by HTTP routers.

    Mirrors the public face of the core ``TaskService``. ``on_event`` is the
    only state write path; the router's ``/events`` endpoint delegates here.
    """

    # --- query face ---
    def get(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_by_user(self, *args: Any, **kwargs: Any) -> Any: ...

    def progress(self, *args: Any, **kwargs: Any) -> Any: ...

    # --- intake face ---
    def create(self, *args: Any, **kwargs: Any) -> Any: ...

    def clarify(self, *args: Any, **kwargs: Any) -> Any: ...

    # --- event-fold / guard face ---
    def on_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def claim_node(self, *args: Any, **kwargs: Any) -> Any: ...

    # --- history / trace face (GET /tasks/{id}/history) ---
    def history(self, *args: Any, **kwargs: Any) -> Any: ...

    def latest_seq(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class TaskSchedulerProtocol(Protocol):
    """Service API for the task orchestration authority — surface used by HTTP
    routers (``/approve`` → ``start``, ``/tick`` → ``tick``).

    Mirrors the public face of the core ``TaskScheduler``.
    """

    def start(self, *args: Any, **kwargs: Any) -> Any: ...

    def tick(self, *args: Any, **kwargs: Any) -> Any: ...

    def on_event(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["TaskSchedulerProtocol", "TaskServiceProtocol"]
