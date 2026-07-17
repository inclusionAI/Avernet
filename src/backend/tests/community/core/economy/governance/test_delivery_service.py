"""Tests for GovernanceDeliveryService — 投递编排域(admin-split-delivery SDD)。

从 GovernanceAdminService 抽出的投递编排服务(deliver_pending/deliver_by_worker/
_run_delivery/create_and_send_reminder)。本文件覆盖:
  - 独立可构造性(只 new 投递服务的 7 个依赖,不拉起 admin 全家桶)。
  - protocol 契约:GovernanceDeliveryService 实现 GovernanceDeliveryServiceProtocol。
  - 方法存在性 + 公开 API 形态。

注:deliver_by_worker / deliver_pending / reminder 的端到端行为用例
(TestDeliverByWorker、test_coverage_supplement 的 TestDeliverPending* 等)
在 Task 2/3 已改用 GovernanceDeliveryService 构造,保留在原测试文件
(test_admin_service.py / test_coverage_supplement.py),复用既有 conftest seed
helper,未做物理搬迁以免大块重定位 diff。本文件作为投递服务的独立测试归属。
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.services.delivery_service import (
    GovernanceDeliveryService,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceDeliveryServiceProtocol,
)

from .conftest import FakeDB, FakeGovernanceConfig, FakeNotifySender


def _build_delivery_svc(engine):
    """独立构造 GovernanceDeliveryService(不依赖 GovernanceAdminService)。

    投递服务注入的 7 个依赖:notify_repo / audit_repo / task_repo / config /
    notify_sender / render_svc / lifecycle_svc。验证 admin 瘦身后投递服务
    自给自足,不再串 admin 的 notify_sender / render_svc 注入。
    """
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo, notify_repo=notify_repo, audit_repo=audit_repo,
    )
    return GovernanceDeliveryService(
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        config=FakeGovernanceConfig(),
        notify_sender=FakeNotifySender(),
        render_svc=NotifyRenderService(),
        lifecycle_svc=lifecycle_svc,
    )


class TestDeliveryServiceConstructible:
    """投递服务可独立构造 + protocol 契约。"""

    def test_independently_constructible(self, engine):
        """只 new 投递服务 7 依赖即可构造,不串 admin。"""
        svc = _build_delivery_svc(engine)
        assert isinstance(svc, GovernanceDeliveryService)

    def test_implements_delivery_protocol(self, engine):
        """GovernanceDeliveryService 满足 GovernanceDeliveryServiceProtocol。"""
        svc = _build_delivery_svc(engine)
        assert isinstance(svc, GovernanceDeliveryServiceProtocol)

    def test_admin_service_no_longer_has_delivery_methods(self):
        """admin_service 瘦身后不再持有投递方法(归投递服务独占)。"""
        from agentclaw.community.core.economy.governance.services.admin_service import (
            GovernanceAdminService,
        )

        for m in ("deliver_pending", "deliver_by_worker", "create_and_send_reminder"):
            assert not hasattr(GovernanceAdminService, m), (
                f"admin_service should no longer expose {m} (moved to delivery)"
            )

    def test_delivery_methods_present(self, engine):
        """投递服务持有迁入的 4 方法(deliver_*/create_and_send_reminder)。"""
        svc = _build_delivery_svc(engine)
        for m in ("deliver_pending", "deliver_by_worker", "create_and_send_reminder"):
            assert callable(getattr(svc, m, None)), f"{m} missing on delivery service"

    def test_admin_service_no_notify_sender_injection(self):
        """admin_service __init__ 不再接受 notify_sender(Task 4 瘦身)。"""
        import inspect

        from agentclaw.community.core.economy.governance.services.admin_service import (
            GovernanceAdminService,
        )

        params = inspect.signature(GovernanceAdminService.__init__).parameters
        assert "notify_sender" not in params, (
            "admin_service must not take notify_sender after split"
        )