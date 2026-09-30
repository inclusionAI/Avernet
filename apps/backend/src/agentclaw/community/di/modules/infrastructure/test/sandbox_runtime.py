"""Sandbox-runtime concern — test binding (NoopSandboxClient)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient


class TestSandboxRuntimeModule(Module):
    """test: NoopSandboxClient (benign neutral values offline)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.sandbox_client import NoopSandboxClient

        binder.bind(SandboxRuntimeClient, to=NoopSandboxClient, scope=singleton)
