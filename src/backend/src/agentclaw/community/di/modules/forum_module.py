"""DI bindings for the phase-1 BBS content core."""

from injector import Binder, Module, singleton

from agentclaw.community.core.forum.browsing import (
    BbsBrowseLoopRunner,
    BbsBrowseLoopScheduler,
)
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.forum.services.forum_service import ForumService
from agentclaw.community.core.repository.implementations.forum import (
    ForumRepository,
)
from agentclaw.community.core.repository.protocols.forum import (
    ForumRepositoryProtocol,
)


class ForumModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(ForumRepositoryProtocol, to=ForumRepository, scope=singleton)
        binder.bind(ForumServiceProtocol, to=ForumService, scope=singleton)
        # BBS Browse Loop — runner (push seam) + scheduler (framework cron).
        # Bound as singletons so lifecycle discovery auto-starts the scheduler.
        binder.bind(BbsBrowseLoopRunner, to=BbsBrowseLoopRunner, scope=singleton)
        binder.bind(BbsBrowseLoopScheduler, to=BbsBrowseLoopScheduler, scope=singleton)
