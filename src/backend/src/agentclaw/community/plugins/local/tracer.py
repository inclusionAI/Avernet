"""Local ``TracerPlugin`` — no tracing in offline/test mode.

``install`` adds nothing and ``current_trace_id`` returns ``None`` ⇒ no
``X-Trace-ID`` header, matching the pre-seam local behavior exactly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.tracer import TracerPlugin
from agentclaw.community.plugins.local._mock_seam import MockSeam

if TYPE_CHECKING:
    from fastapi import FastAPI


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no tracer offline")
class NoopTracer(MockSeam, TracerPlugin):
    """Test/offline double: tracing is unavailable."""

    def install(self, app: "FastAPI") -> None:
        return None

    def current_trace_id(self) -> str | None:
        return None
