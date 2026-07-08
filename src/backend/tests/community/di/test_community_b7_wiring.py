"""Community column wiring for B7 concerns.

Pins that the community profile binds the B7 Protocols (MCP center, MCP auth,
Skill Center client, skill-repo sync, …) to their real community implementations,
and that none is a ``MockSeam`` test double (the community column ships real,
deployable impls — never the local test seams). Grown one concern per task across
the B7 groups (MCP → SkillCenter → skill-scan → approval).
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.plugin_api.skill_scanner import SkillScannerPlugin
from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin
from agentclaw.community.plugin_api.bot_publish_approval import BotPublishApprovalPlugin
from agentclaw.community.plugins.community.mcp_auth import CommunityMCPAuthPlugin
from agentclaw.community.plugins.community.mcp_center import CommunityMCPCenter
from agentclaw.community.plugins.community.skill_center_client import (
    CommunitySkillCenterClient,
)
from agentclaw.community.plugins.community.skill_repo_sync import CommunitySkillRepoSync
from agentclaw.community.plugins.community.skill_scanner import NoopSkillScanner
from agentclaw.community.plugins.community.approval_workflow import NoApprovalWorkflow
from agentclaw.community.plugins.community.bot_publish_approval import DirectPublishApproval
from agentclaw.community.plugins.local._mock_seam import MockSeam


@pytest.fixture(scope="module")
def community_injector():
    return build_injector(profile=DeployProfile.COMMUNITY)


def test_community_binds_mcp_center(community_injector):
    resolved = community_injector.get(MCPCenterPlugin)
    assert isinstance(resolved, CommunityMCPCenter)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_mcp_auth(community_injector):
    resolved = community_injector.get(MCPAuthPlugin)
    assert isinstance(resolved, CommunityMCPAuthPlugin)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_skill_center_client(community_injector):
    resolved = community_injector.get(SkillCenterClient)
    assert isinstance(resolved, CommunitySkillCenterClient)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_skill_repo_sync(community_injector):
    resolved = community_injector.get(SkillRepoSyncPlugin)
    assert isinstance(resolved, CommunitySkillRepoSync)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_skill_scanner(community_injector):
    resolved = community_injector.get(SkillScannerPlugin)
    assert isinstance(resolved, NoopSkillScanner)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_approval_workflow(community_injector):
    resolved = community_injector.get(ApprovalWorkflowPlugin)
    assert isinstance(resolved, NoApprovalWorkflow)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_bot_publish_approval(community_injector):
    resolved = community_injector.get(BotPublishApprovalPlugin)
    assert isinstance(resolved, DirectPublishApproval)
    assert not isinstance(resolved, MockSeam)
