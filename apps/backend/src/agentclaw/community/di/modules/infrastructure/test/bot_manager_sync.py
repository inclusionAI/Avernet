"""Manager-sync doubles for the test/singlebox columns.

Binds the test-only plugin ports for the manager-sync feature:

- ``BcsManagersPlugin`` → the recording fake (no network);
- ``ManagerSyncCoordinator`` → the in-memory coordinator fake;
- ``BotManagerSyncService`` → the real consumer over those doubles plus
  the profile's SQLite ``ManagerSyncReadRepository`` — the binding the
  Rule 25 conformance suites resolve via ``world.get(...)`` (``
  tests/community/contracts/test_bcs_managers.py`` /
  ``test_manager_sync_coordination.py``), so the contract suites pin
  the consumer ↔ Protocol conformance through the same dependency
  graph the application uses. The service itself owns no threads or
  loops; only an explicit ``sync_once`` / ``hold_owner_check`` call
  does work, on the caller's await.

and overrides the composition module's ``ManagerSyncStack`` with a
stack built from those doubles — but **only when the feature is
explicitly enabled**. The default posture stays disabled: this column
binds plugins, it never turns workers on. (Tests that exercise a live
dispatcher install an enabled ``ManagerSyncConfig`` override module;
then and only then does resolution build anything stateful.)

Both fakes are declared test-only in ``plugins/local``; nothing here
talks to Redis, HTTP, or a real BCS.
"""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.modules.bot_manager_sync_module import ManagerSyncStack
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.manager_sync_coordination import (
    ManagerSyncCoordinator,
)
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)


class TestBotManagerSyncModule(Module):
    """test / singlebox: recording + memory doubles for manager sync."""

    def configure(self, binder: Binder) -> None:
        binder.bind(BcsManagersPlugin, to=RecordingBcsManagersPlugin, scope=singleton)
        binder.bind(
            ManagerSyncCoordinator, to=MemoryManagerSyncCoordinator, scope=singleton
        )

    @singleton
    @provider
    @inject
    def manager_sync_service(
        self,
        reader: ManagerSyncReadRepository,
        bcs: BcsManagersPlugin,
        coordinator: ManagerSyncCoordinator,
    ) -> BotManagerSyncService:
        """The world-resolvable consumer of the doubles above (suites only).

        No config gate: a stateless sync service is exactly what the
        conformance suites need, and constructing it binds no worker,
        no loop and no client — the enabled/disabled posture still lives
        one layer up in ``BotManagerSyncProtocol`` / the stack.
        """
        return BotManagerSyncService(reader=reader, bcs=bcs, coordinator=coordinator)

    @singleton
    @provider
    @inject
    def manager_sync_stack(
        self,
        config: ManagerSyncConfig,
        bcs: BcsManagersPlugin,
        coordinator: ManagerSyncCoordinator,
    ) -> ManagerSyncStack:
        """The doubles stack — only stateful when the feature is enabled."""
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        return ManagerSyncStack.available_with(bcs, coordinator)


__all__ = ["TestBotManagerSyncModule"]