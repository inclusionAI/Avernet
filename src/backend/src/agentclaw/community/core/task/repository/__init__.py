"""Task ORM persistence layer (Phase 1).

``core/task/repository/models.py`` — SQLAlchemy models for ``ac_task`` /
``ac_task_event`` / ``ac_task_execution_graph`` on the canonical
``agentclaw.community.core.base.Base``. The repos (Protocol impls) live in
``plugins/task_repository.py`` / ``plugins/task_event_repository.py``; the DDL
mirror for prod provisioning lives in ``core/task/sql/``.
"""
from __future__ import annotations