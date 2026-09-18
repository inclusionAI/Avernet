"""``TaskContextServiceProtocol`` re-export (the task-context Service API surface).

The Protocol is **defined** in its owning core module
``core/task/task_context/task_context_service.py`` and merely re-exported here so
adapters Inject it by name from ``api/`` (repo pattern — see ``api/README.md`` "Where
a Protocol is defined"). ``TaskContextService`` (in ``core/``) implements it; the DI
composition root binds it. This replaces the old ``api/task/task_trajectory_service.py``
protocol (the trajectory read+write contract is now reached only via this facade —
the trajectory sub-module is internal to ``task_context``).

Authoritative: ``src/backend/specs/2026-09-18-task-trajectory-task-context-submodule/
spec.md``.
"""
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextServiceProtocol,
)

__all__ = ["TaskContextServiceProtocol"]
