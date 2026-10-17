"""Distributed periodic scheduler for the BBS content-import task."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.forum.content_import.protocols import (
    ContentImportTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.scheduler import (
    BbsPeriodicTaskScheduler,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_CONTENT_IMPORT_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.di.bbs_digest_config import BbsContentImportTaskConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.cache import CachePlugin


class ContentImportScheduler(
    BbsPeriodicTaskScheduler,
    LifecycleBase,
):
    """Periodically execute ``content_import_task`` with an independent lease."""

    @inject
    def __init__(
        self,
        executor: ContentImportTaskExecutorProtocol,
        cache: CachePlugin,
        config: BbsContentImportTaskConfig,
    ) -> None:
        super().__init__(
            executor=executor,
            cache=cache,
            config=config,
            task=BbsSandboxTask(name=BBS_CONTENT_IMPORT_TASK_NAME),
            lock_key="bbs:content_import",
            label="bbs-content-import",
        )


__all__ = ["ContentImportScheduler"]
