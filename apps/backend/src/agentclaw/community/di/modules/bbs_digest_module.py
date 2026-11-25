"""DI wiring for the backend-owned BBS background tasks."""

from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.core.task.forum.content_import.protocols import (
    ContentImportTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.content_import.scheduler import (
    ContentImportScheduler,
)
from agentclaw.community.core.task.forum.content_import.service import (
    BbsContentImportTaskService,
)
from agentclaw.community.core.task.forum.digest.protocols import (
    DigestTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.digest.scheduler import DigestScheduler
from agentclaw.community.core.task.forum.poll_statistics.protocols import (
    PollStatisticsTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.poll_statistics.scheduler import (
    PollStatisticsScheduler,
)
from agentclaw.community.core.task.forum.poll_statistics.service import (
    BbsPollStatisticsTaskService,
)
from agentclaw.community.core.task.forum.topic_tag.protocols import (
    TopicTagTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.topic_tag.scheduler import TopicTagScheduler
from agentclaw.community.core.task.forum.topic_tag.service import BbsTopicTagTaskService
from agentclaw.community.core.task.forum.digest.service import BbsDigestTaskService
from agentclaw.community.core.task.forum.poll_statistics_protocol import (
    ForumPollStatisticsServiceProtocol,
)
from agentclaw.community.core.task.forum.services.poll_statistics_service import (
    ForumPollStatisticsService,
)
from agentclaw.community.core.task.forum.shared.llm import OpenAICompatibleBbsLLM
from agentclaw.community.core.repository.implementations.task.forum import (
    ForumTopicTagRepository,
)
from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task.forum_topic_tag import (
    ForumTopicTagRepositoryProtocol,
)
from agentclaw.community.core.task.forum.services.topic_tag_service import (
    ForumTopicTagService,
)
from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.core.task.forum.topic_tag_service_protocol import (
    ForumTopicTagServiceProtocol,
)
from agentclaw.community.di.bbs_digest_config import (
    BbsContentImportTaskConfig,
    BbsDigestTaskConfig,
    BbsPollStatisticsTaskConfig,
    BbsTopicTagTaskConfig,
    BbsDingTalkConfig,
    BbsLLMConfig,
)
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.notify_sender import (
    QUALIFIER_BBS_DIGEST,
    NotifySenderPlugin,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.community.notify_sender import (
    CommunityNotifySender,
    DingTalkNotifySender,
    DingTalkSenderConfig,
)


def _read_dingtalk_secret(
    secret_resolver: SecretResolver,
    secret_name: str,
) -> str:
    """Resolve the named BBS DingTalk secret without exposing it in config."""

    try:
        secret = secret_resolver.get_secret(secret_name)
    except Exception as exc:  # noqa: BLE001 - the notification port never raises.
        get_logger().warning(
            "[bbs-digest-notify] DingTalk AK secret lookup failed: %s",
            exc,
        )
        return ""

    value = getattr(secret, "secret_value", None)
    if value is None and isinstance(secret, str):
        value = secret
    return str(value).strip() if value is not None else ""


class BbsDigestModule(Module):
    """Composition root for the backend-owned BBS background-task slices."""

    @singleton
    @provider
    @inject
    def bbs_digest_notify_sender(
        self,
        config: BbsDingTalkConfig,
        secret_resolver: SecretResolver,
    ) -> Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST]:
        """Build the BBS-owned DingTalk card sender instance.

        The bare ``NotifySenderPlugin`` continues to identify the task-discovery
        and governance sender. This qualified binding owns the BBS application:
        it never reads the discovery holders or ``TASK_DISCOVERY_*`` variables.
        An empty config leaves the sender configured as unavailable, so strict
        card delivery returns ``None``.
        """
        return DingTalkNotifySender(
            CommunityNotifySender(),
            config=DingTalkSenderConfig(
                app_key=config.ak_id,
                read_app_secret=lambda: _read_dingtalk_secret(
                    secret_resolver,
                    config.ak_secret_name,
                ),
                robot_code=config.robot_code,
                card_template_id=config.card_template_id,
            ),
            strict_card=True,
        )

    @singleton
    @provider
    @inject
    def bbs_llm(
        self,
        config: BbsLLMConfig,
        secret_resolver: SecretResolver,
        general_http: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> BbsLLMProtocol:
        return OpenAICompatibleBbsLLM(
            base_url=config.base_url,
            secret_name=config.secret_name,
            model=config.model,
            timeout_ms=config.timeout_ms,
            secret_resolver=secret_resolver,
            http_client=general_http,
        )

    @singleton
    @provider
    @inject
    def bbs_topic_tag_repository(
        self,
        db: DatabasePlugin,
    ) -> ForumTopicTagRepositoryProtocol:
        return ForumTopicTagRepository(db=db)

    @singleton
    @provider
    @inject
    def bbs_topic_tag_service(
        self,
        repository: ForumRepositoryProtocol,
        tag_repository: ForumTopicTagRepositoryProtocol,
        llm: BbsLLMProtocol,
    ) -> ForumTopicTagServiceProtocol:
        return ForumTopicTagService(
            repository=repository,
            tag_repository=tag_repository,
            llm=llm,
        )

    @singleton
    @provider
    @inject
    def bbs_digest_task_executor(
        self,
        llm: BbsLLMProtocol,
    ) -> DigestTaskExecutorProtocol:
        return BbsDigestTaskService(llm=llm)

    @singleton
    @provider
    @inject
    def bbs_topic_tag_task_executor(
        self,
        topic_tags: ForumTopicTagServiceProtocol,
    ) -> TopicTagTaskExecutorProtocol:
        return BbsTopicTagTaskService(topic_tags=topic_tags)

    @singleton
    @provider
    @inject
    def bbs_poll_statistics_task_executor(
        self,
        poll_statistics: ForumPollStatisticsServiceProtocol,
    ) -> PollStatisticsTaskExecutorProtocol:
        return BbsPollStatisticsTaskService(poll_statistics=poll_statistics)

    @singleton
    @provider
    @inject
    def bbs_content_import_task_executor(
        self,
        llm: BbsLLMProtocol,
    ) -> ContentImportTaskExecutorProtocol:
        return BbsContentImportTaskService(llm=llm)

    @singleton
    @provider
    @inject
    def bbs_poll_statistics_service(
        self,
        repository: ForumRepositoryProtocol,
    ) -> ForumPollStatisticsServiceProtocol:
        return ForumPollStatisticsService(repository=repository)

    @singleton
    @provider
    @inject
    def bbs_digest_scheduler(
        self,
        task_executor: DigestTaskExecutorProtocol,
        config: BbsDigestTaskConfig,
        cache: CachePlugin,
    ) -> DigestScheduler:
        return DigestScheduler(executor=task_executor, cache=cache, config=config)

    @singleton
    @provider
    @inject
    def bbs_poll_statistics_scheduler(
        self,
        task_executor: PollStatisticsTaskExecutorProtocol,
        config: BbsPollStatisticsTaskConfig,
        cache: CachePlugin,
    ) -> PollStatisticsScheduler:
        return PollStatisticsScheduler(
            executor=task_executor,
            cache=cache,
            config=config,
        )

    @singleton
    @provider
    @inject
    def bbs_topic_tag_scheduler(
        self,
        task_executor: TopicTagTaskExecutorProtocol,
        config: BbsTopicTagTaskConfig,
        cache: CachePlugin,
    ) -> TopicTagScheduler:
        return TopicTagScheduler(
            executor=task_executor,
            cache=cache,
            config=config,
        )

    @singleton
    @provider
    @inject
    def bbs_content_import_scheduler(
        self,
        task_executor: ContentImportTaskExecutorProtocol,
        config: BbsContentImportTaskConfig,
        cache: CachePlugin,
    ) -> ContentImportScheduler:
        return ContentImportScheduler(
            executor=task_executor,
            cache=cache,
            config=config,
        )


__all__ = ["BbsDigestModule"]
