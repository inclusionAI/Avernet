"""TaskPersistenceModule — binds the 5 task repository protocols to their ORM
implementations as singletons.

Profile-independent: the only per-profile difference is the ``DatabasePlugin``
injected into each constructor, which is bound one layer below by the profile's
infrastructure module (CommunityDatabase / SqliteDB / corp ZdasDB). Mirrors
``TaskQueueModule``.
"""

from injector import Binder, Module, singleton

from agentclaw.community.core.repository.implementations.task.task_action_log_repository import (
    TaskActionLogRepository,
)
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
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
from agentclaw.community.core.repository.protocols.task import (
    TaskActionLogRepositoryProtocol,
    TaskCallbackRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
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
