"""Profile → infrastructure module selector.

This is the single concern × profile matrix selector. Branch-local imports keep
community/test import-disjoint from corp SDKs.
"""
from __future__ import annotations

from injector import Module

from engine.community.di.profile import EngineProfile


def modules_for(profile: EngineProfile) -> list[Module]:
    if profile is EngineProfile.COMMUNITY:
        from engine.community.di.infrastructure.auth_gate import CommunityAuthGateModule
        from engine.community.di.infrastructure.claude_code_ws import CommunityClaudeCodeWsModule
        from engine.community.di.infrastructure.notification import CommunityNotificationModule
        from engine.community.di.infrastructure.openclaw_client_proxy import CommunityOpenClawClientProxyModule
        from engine.community.di.modules.infrastructure.shared.openclaw_gateway import OpenClawGatewayModule
        from engine.community.di.infrastructure.work_item import CommunityWorkItemModule

        return [
            CommunityAuthGateModule(),
            CommunityClaudeCodeWsModule(),
            CommunityNotificationModule(),
            CommunityOpenClawClientProxyModule(),
            OpenClawGatewayModule(),
            CommunityWorkItemModule(),
        ]
    if profile is EngineProfile.TEST:
        from engine.community.di.infrastructure_test.auth_gate import TestAuthGateModule
        from engine.community.di.infrastructure_test.claude_code_ws import TestClaudeCodeWsModule
        from engine.community.di.infrastructure_test.notification import TestNotificationModule
        from engine.community.di.infrastructure_test.openclaw_client_proxy import TestOpenClawClientProxyModule
        from engine.community.di.modules.infrastructure.shared.openclaw_gateway import OpenClawGatewayModule
        from engine.community.di.infrastructure_test.work_item import TestWorkItemModule

        return [
            TestAuthGateModule(),
            TestClaudeCodeWsModule(),
            TestNotificationModule(),
            TestOpenClawClientProxyModule(),
            OpenClawGatewayModule(),
            TestWorkItemModule(),
        ]
    if profile is EngineProfile.CORP:
        from importlib import import_module

        from engine.community.di.modules.infrastructure.shared.openclaw_gateway import OpenClawGatewayModule

        # String import (no static community -> corp edge): the corp bootstrap is
        # absent from a community build. It self-registers runtime hooks and
        # returns the corp infrastructure DI column.
        corp_bootstrap = import_module("engine.corp.di.corp_bootstrap")
        corp_bootstrap.install_corp_runtime_hooks()

        return [*corp_bootstrap.build_corp_modules(), OpenClawGatewayModule()]
    raise ValueError(f"Unhandled engine profile: {profile!r}")
