"""Skill-center-client concern — test / singlebox binding (local stub)."""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin


logger = get_logger()


class TestSkillCenterClientModule(Module):
    """test / singlebox: local stubs."""

    @singleton
    @provider
    def skill_center_client(self) -> SkillCenterClient:
        from agentclaw.community.plugins.local.skill_center_client import (
            LocalSkillCenterClient,
        )

        logger.info("SkillCenterClient: LocalSkillCenterClient (test)")
        return LocalSkillCenterClient()

    @singleton
    @provider
    def skill_scanner(self) -> SkillScannerPlugin:
        from agentclaw.community.plugins.local.skill_scanner import LocalSkillScanner

        return LocalSkillScanner()
