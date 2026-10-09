"""Community ``SkillScannerPlugin`` — no scanner SDK in the community build.

A real, deployable impl (not a ``MockSeam`` test double): the community build ships
without the proprietary scanner SDK, so static scanning is unavailable and
auto-detection of MCP dependencies / risk tags is skipped. Skills may still declare
their MCP dependencies in their own metadata.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin


class NoopSkillScanner(SkillScannerPlugin):
    """Scanning unavailable in the community profile."""

    def is_available(self) -> bool:
        return False

    def create_sdk(self) -> Any | None:
        return None
