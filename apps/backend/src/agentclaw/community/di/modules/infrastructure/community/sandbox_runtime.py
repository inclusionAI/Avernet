"""Sandbox-runtime concern — community binding (CommunitySandboxClient)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient


class CommunitySandboxRuntimeModule(Module):
    """community: CommunitySandboxClient (no ARCA runtime ⇒ ops raise)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.sandbox_client import CommunitySandboxClient

        binder.bind(SandboxRuntimeClient, to=CommunitySandboxClient, scope=singleton)
