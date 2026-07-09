"""Unit tests for the community ``CommunityMCPAuthPlugin`` (B7).

Permissive MCP auth: no remote permission/IAM service. Every check is granted;
token exchange is a no-op.
"""
from __future__ import annotations

from agentclaw.community.plugins.community.mcp_auth import CommunityMCPAuthPlugin


def test_check_permission_grants_server_and_tools():
    auth = CommunityMCPAuthPlugin()
    result = auth.check_permission("user-1", "mcp.alpha", tool_list=["t1", "t2"])
    assert result == {"mcp.alpha": True, "t1": True, "t2": True}


def test_check_permission_without_tools():
    auth = CommunityMCPAuthPlugin()
    assert auth.check_permission("u", "mcp.alpha") == {"mcp.alpha": True}


def test_apply_permission_succeeds():
    auth = CommunityMCPAuthPlugin()
    assert auth.apply_permission("u", "mcp.alpha", ["t1"]) == {
        "success": True,
        "process_url": None,
        "error": None,
    }


def test_query_permission_status_all_authorized():
    auth = CommunityMCPAuthPlugin()
    statuses = auth.query_permission_status("u", "mcp.alpha", tool_list=["t1"])
    assert [s["auth_state"] for s in statuses] == ["AUTHORIZED", "AUTHORIZED"]
    assert {s["resource_code"] for s in statuses} == {"mcp.alpha", "mcp.alpha/t1"}


def test_exchange_iam_token_is_noop():
    auth = CommunityMCPAuthPlugin()
    assert auth.exchange_iam_token("any-token") is None
