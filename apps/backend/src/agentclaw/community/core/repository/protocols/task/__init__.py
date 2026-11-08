"""Repository contracts for the collaboration-task persistence layer.

The task-specific contracts live beside ``forum`` and ``feedback`` in this
package; ``core`` holds the original task-persistence contracts and is
re-exported here so existing ``...repository.protocols.task`` imports remain
stable.
"""

from agentclaw.community.core.repository.protocols.task.artifact import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.core import (
    TaskActionLogRepositoryProtocol,
    TaskCallbackCorrelationRepositoryProtocol,
    TaskCallbackRepositoryProtocol,
    TaskDiscoveryLockRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
    TaskTrajectoryRepositoryProtocol,
)

__all__ = [
    "TaskActionLogRepositoryProtocol",
    "TaskArtifactRepositoryProtocol",
    "TaskCallbackCorrelationRepositoryProtocol",
    "TaskCallbackRepositoryProtocol",
    "TaskDiscoveryLockRepositoryProtocol",
    "TaskGraphRepositoryProtocol",
    "TaskInfoRepositoryProtocol",
    "TaskNodeRelationRepositoryProtocol",
    "TaskNodeRepositoryProtocol",
    "TaskNodeRunInfoRepositoryProtocol",
    "TaskTrajectoryRepositoryProtocol",
]
