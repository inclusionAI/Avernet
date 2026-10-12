"""DI bindings for the phase-1 BBS content core."""

from injector import Binder, Module, provider, singleton

from agentclaw.community.core.forum.browsing import (
    BbsBrowseLoopRunner,
    BbsBrowseLoopScheduler,
)
from agentclaw.community.core.forum.browsing.cron_setup import BbsBrowseCronManager
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.forum.services.forum_service import ForumService
from agentclaw.community.core.repository.implementations.forum import (
    ForumRepository,
)
from agentclaw.community.core.repository.protocols.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.di.config import TaskDispatchConfig


class ForumModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ForumRepositoryProtocol, to=ForumRepository, scope=singleton)
        binder.bind(ForumServiceProtocol, to=ForumService, scope=singleton)
        # BBS Browse Loop — runner (push seam) + scheduler (framework cron).
        # Bound as singletons so lifecycle discovery auto-starts the scheduler.
        binder.bind(BbsBrowseLoopScheduler, to=BbsBrowseLoopScheduler, scope=singleton)
        # Backend-created Bot-side cron for openclaw subscriptions (B scheme).
        binder.bind(BbsBrowseCronManager, to=BbsBrowseCronManager, scope=singleton)

    @singleton
    @provider
    def bbs_browse_loop_runner(
        self,
        bot: OpenApiBotPort,
        service: ForumServiceProtocol,
        task_dispatch: TaskDispatchConfig,
    ) -> BbsBrowseLoopRunner:
        return BbsBrowseLoopRunner(
            bot,
            service,
            backend_url=task_dispatch.backend_url,
        )
