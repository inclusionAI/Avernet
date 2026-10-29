"""Risk queue composition. Profiles may replace the unavailable provider."""

from datetime import datetime, timezone

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_collaborator.protocols import CollaboratorServiceProtocol
from agentclaw.community.core.bot_public_risk.advance import ApprovalRecovery, RiskAdvance, RiskPendingHandler
from agentclaw.community.core.bot_public_risk.application import RiskInputBuilder
from agentclaw.community.core.bot_public_risk.approval_guard import RiskApprovalGuard
from agentclaw.community.core.bot_public_risk.eligibility import RiskEligibility
from agentclaw.community.core.bot_public_risk.lifecycle import RiskTaskLifecycle
from agentclaw.community.core.bot_public_risk.notices import (
    RiskNoticeDelivery,
    WorkOrderRiskNoticeSender,
)
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskRepository
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository
from agentclaw.community.core.bot_public_risk.publish_adapter import RiskPublishAdapter
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_public_risk.tasks import (
    RiskTasks,
    SecurityProvider,
    SecurityProviderUnavailable,
    SecuritySubmission,
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
    def submit_agent_scan(self, agent_info: dict) -> SecuritySubmission:
        raise SecurityProviderUnavailable("risk security provider unavailable")

    def get_agent_check_report(self, check_id: str):
        raise SecurityProviderUnavailable("risk security provider unavailable")


class LazyRiskInputs:
    def __init__(self, injector: Injector):
        self.injector = injector

    def build(self, *, bot, target):
        return self.injector.get(RiskInputBuilder).build(bot=bot, target=target)


class UnavailableApprovalRecovery:
    def __call__(self, row):
        return None

class BotPublicRiskModule(Module):
    def configure(self, binder: Binder):
        binder.bind(SecurityProvider, to=UnavailableRiskProvider, scope=singleton)
        binder.bind(ApprovalRecovery, to=UnavailableApprovalRecovery, scope=singleton)
        binder.bind(
            RiskRepository, to=BotPublicRiskRepository, scope=singleton
        )

    @singleton
    @provider
    @inject
    def risk_tasks(
        self,
        repo: RiskRepository,
        queue: TaskQueueService,
        provider: SecurityProvider,
    ) -> RiskTasks:
        return RiskTasks(repo, queue, provider, utc_now)

    @singleton
    @provider
    @inject
    def risk_notice_delivery(
        self, repo: RiskRepository, work_orders: WorkOrderServiceProtocol
    ) -> RiskNoticeDelivery:
        return RiskNoticeDelivery(repo, WorkOrderRiskNoticeSender(work_orders), utc_now)

    @singleton
    @provider
    def risk_eligibility(self, bots: BotRepository, collaborators: CollaboratorServiceProtocol,
                         injector: Injector) -> RiskEligibility:
        return RiskEligibility(bots, collaborators, LazyRiskInputs(injector))

    @singleton
    @provider
    def risk_approval_guard(self, repo: RiskRepository,
                            eligibility: RiskEligibility) -> RiskApprovalGuard:
        return RiskApprovalGuard(repo, eligibility, utc_now)

    @singleton
    @provider
    def risk_advance(self, repo: RiskRepository, eligibility: RiskEligibility,
                     bots: BotRepository, public: BotPublicServiceProtocol,
                     recovery: ApprovalRecovery) -> RiskAdvance:
        return RiskAdvance(repo, eligibility, RiskPublishAdapter(bots, public), utc_now, recovery=recovery)

    @singleton
    @provider
    @inject
    def risk_lifecycle(
        self,
        registry: HandlerRegistry,
        tasks: RiskTasks,
        delivery: RiskNoticeDelivery,
        repo: RiskRepository,
        config: TaskQueueWorkerConfig,
        advance: RiskAdvance,
        bots: BotRepository,
        public: BotPublicServiceProtocol,
        guard: RiskApprovalGuard,
    ) -> RiskTaskLifecycle:
        return RiskTaskLifecycle(
            registry=registry,
            tasks=tasks,
            delivery=delivery,
            repo=repo,
            env_provider=get_current_env,
            worker_enabled=config.enabled,
            clock=utc_now,
            advance=advance,
            pending_handler=RiskPendingHandler(repo, bots, public, guard, get_current_env),
        )
