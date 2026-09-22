"""CronAutoSetupService 的 injector 构造语义 sanity（review flag）。

验证 ``Optional[AicodingHostedWorkspaceServiceProtocol] = None`` 构造槽：
binding 存在时（生产上 ``AICodingModule`` 在 DI base list 恒装，
``AicodingHostedWorkspaceServiceProtocol`` 全局绑定）injector 注入实现实例，
而非静默留在默认 ``None``。``None`` 默认只用于不经 injector 的手工构造
（单测 / 直接传参），此时补建路径按设计降级跳过。
"""
from __future__ import annotations

from injector import Injector, Module

from agentclaw.community.core.aicoding.protocols import (
    AicodingHostedWorkspaceServiceProtocol,
)
from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import (
    CronAutoSetupService,
)
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.repository.protocols.bot import TemplateRepository


class _FakeTemplateRepo:
    def get_by_bot_id(self, bot_id):  # pragma: no cover - 注入语义测试不触达
        return None


class _FakeRelay:
    pass


class _FakeHostedWorkspaceService:
    def ensure_hosted_workspace(self, bot_id, user_id):  # pragma: no cover
        return None


class _CronInjectionModule(Module):
    """最小 wiring：CronAutoSetupService 三个构造依赖的真实绑定。"""

    def configure(self, binder):
        binder.bind(TemplateRepository, to=_FakeTemplateRepo)
        binder.bind(CronRelayService, to=_FakeRelay)
        binder.bind(
            AicodingHostedWorkspaceServiceProtocol, to=_FakeHostedWorkspaceService
        )


class TestCronAutoSetupInjection:
    def test_optional_protocol_slot_injected_when_bound(self):
        """binding 存在 → Optional[Protocol] 槽注入实现，不静默停在 None。"""
        svc = Injector([_CronInjectionModule()]).get(CronAutoSetupService)
        assert isinstance(svc._hosted_workspace_service, _FakeHostedWorkspaceService)

    def test_manual_construction_defaults_to_none(self):
        """不经 injector 的手工构造 → 默认 None，补建路径按设计降级跳过。"""
        svc = CronAutoSetupService(
            template_repository=_FakeTemplateRepo(),
            cron_relay_service=_FakeRelay(),
        )
        assert svc._hosted_workspace_service is None