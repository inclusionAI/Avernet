"""Local ``SkillScannerPlugin`` — no scanner SDK in offline/test mode."""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no scan SDK offline")
class LocalSkillScanner(SkillScannerPlugin):
    """Test/offline double: scanning is unavailable."""

    def is_available(self) -> bool:
        return False

    def create_sdk(self) -> Any | None:
        return None
