"""Distributed periodic scheduler for the deterministic BBS poll task."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.forum.poll_statistics.protocols import (
    PollStatisticsTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.scheduler import (
    BbsPeriodicTaskScheduler,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_POLL_STATISTICS_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.di.bbs_digest_config import BbsPollStatisticsTaskConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.cache import CachePlugin


class PollStatisticsScheduler(BbsPeriodicTaskScheduler, LifecycleBase):
    """Project POLL votes while holding ``bbs:poll_statistics``."""

    @inject
    def __init__(
        self,
        executor: PollStatisticsTaskExecutorProtocol,
        cache: CachePlugin,
        config: BbsPollStatisticsTaskConfig,
    ) -> None:
        super().__init__(
            executor=executor,
            cache=cache,
            config=config,
            task=BbsSandboxTask(name=BBS_POLL_STATISTICS_TASK_NAME),
            lock_key="bbs:poll_statistics",
            label="bbs-poll-statistics",
        )


__all__ = ["PollStatisticsScheduler"]
