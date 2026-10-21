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
    BbsDingTalkConfig,
    BbsLLMConfig,
)
from agentclaw.community.plugin_api.bbs_digest_notify import (
    BbsDigestNotifyProvider,
)
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


class BbsDigestModule(Module):
    """Composition root for the BBS digest and content-import runtime slices."""

    @singleton
    @provider
    @inject
    def bbs_digest_notify_provider(
        self,
        config: BbsDingTalkConfig,
        secret_resolver: SecretResolver,
    ) -> BbsDigestNotifyProvider:
        """Bind the BBS-owned card sender; discovery account holders stay unused."""

        from agentclaw.community.plugins.community.bbs_digest_notify import (
            BbsDingTalkNotifyProvider,
        )

        return BbsDingTalkNotifyProvider(
            config=config,
            secret_resolver=secret_resolver,
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
        poll_statistics: ForumPollStatisticsServiceProtocol,
        topic_tags: ForumTopicTagServiceProtocol,
    ) -> DigestTaskExecutorProtocol:
        return BbsDigestTaskService(
            llm=llm,
            poll_statistics=poll_statistics,
            topic_tags=topic_tags,
        )

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
