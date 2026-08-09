"""DI bindings for the distributed task queue.

Wires the unified repository (bound directly like ``BotPublishRepository`` —
no ``@plugin_impl``), the handler registry, the enqueue service, and the
``TaskWorker``. ``TaskWorker`` is a singleton implementing ``Lifecycle``, so
``discover_lifecycle_participants`` finds it at app startup and runs its
``startup()`` / ``shutdown()`` — no explicit lifecycle list to maintain.

The ``HandlerRegistry`` singleton is created empty. BaaS and Teclaw register
their production handlers in their own ``Lifecycle.bootstrap()`` methods
(which the lifespan runner completes before any ``startup()``), so the worker
sees a populated registry before it claims. The worker remains disabled by
default until an environment provisions ``ac_task_queue`` and explicitly
enables it.
"""
from injector import Binder, Module, singleton

from agentclaw.community.core.repository.protocols.platform import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.plugins.task_queue_repository import TaskQueueRepository


class TaskQueueModule(Module):
    """Bindings for the task_queue component (profile-independent)."""

    def configure(self, binder: Binder) -> None:
        # Unified ORM repo — same instance serves SQLite (local) and OceanBase
        # (prod) via the injected DatabasePlugin.
        binder.bind(
            TaskQueueRepositoryProtocol,
            to=TaskQueueRepository,
            scope=singleton,
        )
        # One registry shared by all participants (adopters + worker).
        binder.bind(HandlerRegistry, to=HandlerRegistry, scope=singleton)
        # Enqueue facade for adopters.
        binder.bind(TaskQueueService, to=TaskQueueService, scope=singleton)
        # The in-process worker — a Lifecycle singleton, auto-discovered.
        binder.bind(TaskWorker, to=TaskWorker, scope=singleton)
