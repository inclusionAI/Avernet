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
from agentclaw.community.core.task.forum.shared.llm import OpenAICompatibleBbsLLM
from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.di.bbs_digest_config import (
    BbsContentImportTaskConfig,
    BbsDigestTaskConfig,
    BbsLLMConfig,
)
from agentclaw.community.plugin_api.cache import CachePlugin
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
    def bbs_digest_task_executor(
        self,
        llm: BbsLLMProtocol,
    ) -> DigestTaskExecutorProtocol:
        return BbsDigestTaskService(llm=llm)

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
