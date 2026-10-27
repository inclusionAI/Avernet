"""Risk queue composition. Profiles may replace the unavailable provider."""

from datetime import datetime, timezone

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_public_risk.lifecycle import RiskTaskLifecycle
from agentclaw.community.core.bot_public_risk.notices import (
    RiskNoticeDelivery,
    WorkOrderRiskNoticeSender,
)
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository
from agentclaw.community.core.bot_public_risk.tasks import (
    RiskTasks,
    SecurityProvider,
    SecurityProviderUnavailable,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.work_orders.work_order_service_protocol import (
    WorkOrderServiceProtocol,
)
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.utils.env_utils import get_current_env


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UnavailableRiskProvider:
    def submit_agent_scan(self, agent_info: dict) -> str:
        raise SecurityProviderUnavailable("risk security provider unavailable")

    def get_agent_check_report(self, check_id: str):
        raise SecurityProviderUnavailable("risk security provider unavailable")


class BotPublicRiskModule(Module):
    def configure(self, binder: Binder):
        binder.bind(SecurityProvider, to=UnavailableRiskProvider, scope=singleton)
        binder.bind(
            BotPublicRiskRepository, to=BotPublicRiskRepository, scope=singleton
        )

    @singleton
    @provider
    @inject
    def risk_tasks(
        self,
        repo: BotPublicRiskRepository,
        queue: TaskQueueService,
        provider: SecurityProvider,
    ) -> RiskTasks:
        return RiskTasks(repo, queue, provider, utc_now)

    @singleton
    @provider
    @inject
    def risk_notice_delivery(
        self, repo: BotPublicRiskRepository, work_orders: WorkOrderServiceProtocol
    ) -> RiskNoticeDelivery:
        return RiskNoticeDelivery(repo, WorkOrderRiskNoticeSender(work_orders), utc_now)

    @singleton
    @provider
    @inject
    def risk_lifecycle(
        self,
        registry: HandlerRegistry,
        tasks: RiskTasks,
        delivery: RiskNoticeDelivery,
        repo: BotPublicRiskRepository,
        config: TaskQueueWorkerConfig,
    ) -> RiskTaskLifecycle:
        return RiskTaskLifecycle(
            registry=registry,
            tasks=tasks,
            delivery=delivery,
            repo=repo,
            env_provider=get_current_env,
            worker_enabled=config.enabled,
            clock=utc_now,
        )
