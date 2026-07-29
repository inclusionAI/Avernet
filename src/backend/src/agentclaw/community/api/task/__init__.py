"""Task module API contracts — re-export of the core Protocols (Phase 0.5).

The Protocols + DTOs live in :mod:`agentclaw.community.core.task.protocols` so
that core services and plugins may depend on / implement them without importing
the api layer (four-layer rule: core + plugins must NOT import api). This
package re-exports them so the router / DI composition root (which MAY import
core) references ``api.task.TaskService`` etc. as the DI binding keys.
"""
from __future__ import annotations

from agentclaw.community.core.task.protocols import (
    BbsExecutor,
    BcsCollaborationProtocol,
    BotCandidate,
    BotDiscoverPort,
    DecomposerPort,
    DispatchResult,
    ExecutionPort,
    PanelDeliveryPort,
    PanelEventPublisher,
    PanelMessage,
    RouteRecommendation,
    TaskDriverPort,
    TaskScheduler,
    TaskService,
)

__all__ = [
    "BbsExecutor",
    "BcsCollaborationProtocol",
    "BotCandidate",
    "BotDiscoverPort",
    "DecomposerPort",
    "DispatchResult",
    "ExecutionPort",
    "PanelDeliveryPort",
    "PanelEventPublisher",
    "PanelMessage",
    "RouteRecommendation",
    "TaskDriverPort",
    "TaskScheduler",
    "TaskService",
]