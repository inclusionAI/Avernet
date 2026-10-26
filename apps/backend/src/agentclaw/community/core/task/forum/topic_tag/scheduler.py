"""Distributed periodic scheduler for automatic BBS Topic tagging."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.forum.shared.scheduler import (
    BbsPeriodicTaskScheduler,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_TOPIC_TAG_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.core.task.forum.topic_tag.protocols import (
    TopicTagTaskExecutorProtocol,
)
from agentclaw.community.di.bbs_digest_config import BbsTopicTagTaskConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.cache import CachePlugin


class TopicTagScheduler(BbsPeriodicTaskScheduler, LifecycleBase):
    """Classify untagged Topics while holding ``bbs:topic_tags``."""

    @inject
    def __init__(
        self,
        executor: TopicTagTaskExecutorProtocol,
        cache: CachePlugin,
        config: BbsTopicTagTaskConfig,
    ) -> None:
        super().__init__(
            executor=executor,
            cache=cache,
            config=config,
            task=BbsSandboxTask(name=BBS_TOPIC_TAG_TASK_NAME),
            lock_key="bbs:topic_tags",
            label="bbs-topic-tag",
        )


__all__ = ["TopicTagScheduler"]
