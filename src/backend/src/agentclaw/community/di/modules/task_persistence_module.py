"""TaskPersistenceModule — binds the 5 task repository protocols to their ORM
implementations as singletons.

Profile-independent: the only per-profile difference is the ``DatabasePlugin``
injected into each constructor, which is bound one layer below by the profile's
infrastructure module (CommunityDatabase / SqliteDB / corp ZdasDB). Mirrors
``TaskQueueModule``.

The trajectory read-side ``TaskTrajectoryAssembler`` (REQ-8, P4) is bound here
too — it consumes ONLY ``TaskTrajectoryRepositoryProtocol`` (bound just above),
is a lightweight DI constructable service-layer object, and co-locating the
binding with the trajectory repo keeps the trajectory read-side wiring in one
place so P5's analysis service can ``Injected(...)`` it (per the P4 task's
"lean toward DI registration" guidance).
"""

from injector import Binder, Module, singleton

from agentclaw.community.core.repository.implementations.task.task_action_log_repository import (
    TaskActionLogRepository,
)
from agentclaw.community.core.repository.implementations.task.task_callback_correlation_repository import (
    TaskCallbackCorrelationRepository,
)
from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_relation_repository import (
    TaskNodeRelationRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskActionLogRepositoryProtocol,
    TaskCallbackCorrelationRepositoryProtocol,
    TaskCallbackRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)


class TaskPersistenceModule(Module):
    """Bind the 5 task repository contracts to their unified ORM implementations."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            TaskActionLogRepositoryProtocol, to=TaskActionLogRepository, scope=singleton
        )
        binder.bind(
            TaskGraphRepositoryProtocol, to=TaskGraphRepository, scope=singleton
        )
        binder.bind(TaskInfoRepositoryProtocol, to=TaskInfoRepository, scope=singleton)
        binder.bind(TaskNodeRepositoryProtocol, to=TaskNodeRepository, scope=singleton)
        binder.bind(
            TaskNodeRunInfoRepositoryProtocol,
            to=TaskNodeRunInfoRepository,
            scope=singleton,
        )
        binder.bind(
            TaskNodeRelationRepositoryProtocol,
            to=TaskNodeRelationRepository,
            scope=singleton,
        )
        binder.bind(
            TaskCallbackRepositoryProtocol,
            to=TaskCallbackRepository,
            scope=singleton,
        )
        binder.bind(
            TaskTrajectoryRepositoryProtocol,
            to=TaskTrajectoryRepository,
            scope=singleton,
        )
        # Trajectory read-side assembler (REQ-8, P4): consumes the trajectory
        # repo (TaskTrajectoryRepositoryProtocol → TaskTrajectoryRepository
        # above); light DI-constructable service-layer object. Bound alongside
        # the trajectory repo so P5's analysis service can Injected(...) it.
        binder.bind(TaskTrajectoryAssembler, to=TaskTrajectoryAssembler, scope=singleton)
        binder.bind(
            TaskCallbackCorrelationRepositoryProtocol,
            to=TaskCallbackCorrelationRepository,
            scope=singleton,
        )
