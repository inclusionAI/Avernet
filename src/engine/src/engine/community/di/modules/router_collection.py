"""DI-collected router list (``List[APIRouter]`` multibind).

The mounted open-source router surface is profile-invariant: every profile
installs ``SharedRoutersModule`` and therefore contributes the same FastAPI
routers. This OSS architecture intentionally exposes only OpenClaw and
Claude Code delivery routes; AICoding is not mounted here.

Profile differences must live behind router dependencies (ports/services) and
be selected by profile-specific DI modules, not by mounting different routers.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from injector import Module, multiprovider, singleton

from engine.community.api.routers.claude_code_ws import router as claude_code_ws_router
from engine.community.openclaw.router import router as openclaw_client_router


class SharedRoutersModule(Module):
    """Routers mounted on every profile; divergence is behind DI-bound ports."""

    @multiprovider
    @singleton
    def routers(self) -> List[APIRouter]:
        return [
            claude_code_ws_router,
            openclaw_client_router,
        ]
