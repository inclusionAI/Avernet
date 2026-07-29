"""Task concern — application services (Phase 2).

``TaskService`` is the unified authority over the :class:`Task` aggregate:
intake/plan (``create`` / ``amend`` / ``finalize_plan``), event-fold/guard
(``on_event`` / ``claim_node``), and the read-only query + secondary-panel face
(``get`` / ``list_by_user`` / ``progress`` / ``get_task_graph`` /
``get_node_detail`` / ``get_sub_dag`` / ``subscribe_task_graph``).

It holds NO编排 decision — that is :class:`TaskScheduler`'s job (Phase 3) via the
Driver/Discover/Decomposer/Execution Ports. State writes flow exclusively
through ``on_event`` (single writer = :class:`TaskEventRepo`); the
state_machine guard is the only authority on legal moves.
"""
from __future__ import annotations

from .bbs_executor import BbsExecutorService
from .graph_adapter import SmGraphAdapter
from .task_scheduler import TaskScheduler
from .task_service import TaskService

__all__ = [
    "BbsExecutorService",
    "SmGraphAdapter",
    "TaskScheduler",
    "TaskService",
]