"""Task module service API — the adapter-facing service Protocols.

These loose (``*args, **kwargs -> Any``) Protocols live in
:mod:`agentclaw.community.api.task.service_api` so the api layer imports **no**
core code (``adapters → api``; api↔core decoupled via DI). DI binds the api
Protocols to the concrete core services, which structurally satisfy them
(conformance enforced by
``tests/community/architecture/test_task_service_api_conformance.py``) — mirrors
``api/bot_service.py: BotServiceProtocol``.

The Port Protocols (Discover/Decomposer/Driver/Execution/BbsCollab/Panel) and
the core-internal ``TaskService``/``TaskScheduler`` Protocols live in
:mod:`agentclaw.community.core.task.protocols`; core, plugins and the DI
composition root import them **from there**, not from this package — api must
not depend on core, so this package re-exports nothing from core.
"""
from __future__ import annotations

from agentclaw.community.api.task.service_api import (
    TaskSchedulerProtocol,
    TaskServiceProtocol,
)

__all__ = ["TaskSchedulerProtocol", "TaskServiceProtocol"]
