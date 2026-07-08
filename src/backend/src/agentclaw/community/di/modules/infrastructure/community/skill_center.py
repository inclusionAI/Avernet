"""Skill-center concern — community bindings.

B7 bypasses the Skill Center marketplace (unused in production) with a real
"unsupported" client. The skills source is the community ``SkillRepoSyncPlugin``
(a local skills directory); skill scanning is a no-op. The corp git→ObjectStorage
``GitSyncService`` stays corp-only — in community it is bound but inert (no repo
URL configured).
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin


class CommunitySkillCenterClientModule(Module):
    """community: Skill Center bypass + local-dir SkillRepoSync + no scanner."""

    @singleton
    @provider
    def skill_center_client(self) -> SkillCenterClient:
        from agentclaw.community.plugins.community.skill_center_client import (
            CommunitySkillCenterClient,
        )

        return CommunitySkillCenterClient()

    @singleton
    @provider
    def skill_repo_sync(self) -> SkillRepoSyncPlugin:
        # Real local-dir impl (not a MockSeam double): reads the host skills
        # directory; no cloud OSS / git-server dependency.
        from agentclaw.community.plugins.community.skill_repo_sync import (
            CommunitySkillRepoSync,
        )

        return CommunitySkillRepoSync()

    @singleton
    @provider
    def skill_scanner(self) -> SkillScannerPlugin:
        # No proprietary scan SDK in the community build — scanning is skipped.
        from agentclaw.community.plugins.community.skill_scanner import NoopSkillScanner

        return NoopSkillScanner()
