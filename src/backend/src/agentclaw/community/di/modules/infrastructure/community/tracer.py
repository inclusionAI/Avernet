"""Tracer concern — community binding (CommunityTracer)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.tracer import TracerPlugin


class CommunityTracerModule(Module):
    """community: CommunityTracer (self-minted per-request id, no exporter)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.tracer import CommunityTracer

        binder.bind(TracerPlugin, to=CommunityTracer, scope=singleton)
