from __future__ import annotations

from injector import Module, provider, singleton

from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.plugins.auth_gate.noop_impl import NoopAuthGateService


class CommunityAuthGateModule(Module):
    @singleton
    @provider
    def auth_gate(self) -> AuthGateService:
        return NoopAuthGateService()
