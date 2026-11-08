"""Distributed periodic scheduler for the backend-owned BBS digest task."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.forum.digest.protocols import (
    DigestTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.scheduler import (
    BbsPeriodicTaskScheduler,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_DIGEST_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.di.bbs_digest_config import BbsDigestTaskConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.cache import CachePlugin


class DigestScheduler(BbsPeriodicTaskScheduler, LifecycleBase):
    """Periodically execute ``digest_task`` while holding ``bbs:summary``."""

    @inject
    def __init__(
        self,
        executor: DigestTaskExecutorProtocol,
        cache: CachePlugin,
        config: BbsDigestTaskConfig,
    ) -> None:
        super().__init__(
            executor=executor,
            cache=cache,
            config=config,
            task=BbsSandboxTask(name=BBS_DIGEST_TASK_NAME),
            lock_key="bbs:summary",
            label="bbs-digest",
        )


__all__ = ["DigestScheduler"]
