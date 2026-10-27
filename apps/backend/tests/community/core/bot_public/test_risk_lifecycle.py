import asyncio
from datetime import datetime

from agentclaw.community.core.bot_public_risk.lifecycle import RiskTaskLifecycle
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.bot_public_risk.tasks import (
    SUBMIT,
    DETECTING,
    REJECT,
    NOTICE,
)


def test_bootstrap_registers_risk_handlers():
    registry = HandlerRegistry()
    lifecycle = RiskTaskLifecycle(
        registry=registry,
        tasks=object(),
        delivery=object(),
        repo=object(),
        env_provider=lambda: "dev",
        worker_enabled=False,
        clock=lambda: datetime(2026, 10, 28),
    )
    asyncio.run(lifecycle.bootstrap())
    assert all(
        registry.get(kind) is not None for kind in (SUBMIT, DETECTING, REJECT, NOTICE)
    )


def test_recovery_scans_each_tenant_with_independent_cursors():
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

    calls = []

    class Repo:
        def list_recovery_tenants(self, *, env):
            assert env == "dev"
            return ("alpha", "beta")

    class Tasks:
        def recover(self, *, env, now, after_id):
            calls.append((get_current_avernet_tenant(), "work", after_id))
            return "cursor" if after_id == "" else None

        def recover_notices(self, *, env, now, after_id):
            calls.append((get_current_avernet_tenant(), "notice", after_id))
            return None

    lifecycle = RiskTaskLifecycle(
        registry=None,
        tasks=Tasks(),
        delivery=None,
        repo=Repo(),
        env_provider=lambda: "dev",
        worker_enabled=True,
        clock=lambda: datetime(2026, 10, 28),
    )
    lifecycle.reconcile_once()
    assert calls == [
        ("alpha", "work", ""),
        ("alpha", "work", "cursor"),
        ("alpha", "notice", ""),
        ("beta", "work", ""),
        ("beta", "work", "cursor"),
        ("beta", "notice", ""),
    ]
    assert get_current_avernet_tenant() == "teamclaw"


def test_application_injector_discovers_risk_lifecycle_in_test_profile():
    from agentclaw.community.di.container import build_injector
    from agentclaw.community.di.profile import DeployProfile
    from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants

    injector = build_injector(profile=DeployProfile.TEST)
    lifecycle = injector.get(RiskTaskLifecycle)
    assert lifecycle in discover_lifecycle_participants(injector)
    asyncio.run(lifecycle.bootstrap())
    assert lifecycle.registry.get(SUBMIT) is not None


def test_community_injector_resolves_unavailable_provider():
    from agentclaw.community.di.container import build_injector
    from agentclaw.community.di.profile import DeployProfile
    from agentclaw.community.di.modules.bot_public_risk_module import (
        UnavailableRiskProvider,
    )
    from agentclaw.community.core.bot_public_risk.tasks import SecurityProvider

    injector = build_injector(profile=DeployProfile.COMMUNITY)
    assert isinstance(injector.get(SecurityProvider), UnavailableRiskProvider)
    assert injector.get(RiskTaskLifecycle) is not None


def test_injector_registers_advance_and_replay_with_fail_closed_inputs():
    import pytest
    from agentclaw.community.di.container import build_injector
    from agentclaw.community.di.profile import DeployProfile
    from agentclaw.community.core.bot_public_risk.application import RiskInputBuilder
    from agentclaw.community.core.bot_public_risk.tasks import ADVANCE, REPLAY
    from agentclaw.community.core.bot_public_risk.input_builder import McpProjectionUnavailable

    injector = build_injector(profile=DeployProfile.TEST)
    lifecycle = injector.get(RiskTaskLifecycle)
    asyncio.run(lifecycle.bootstrap())
    assert lifecycle.registry.get(ADVANCE) is not None
    assert lifecycle.registry.get(REPLAY) is not None
    with pytest.raises(McpProjectionUnavailable, match="MCP risk projection is not configured"):
        injector.get(RiskInputBuilder).build(bot={}, target={})
