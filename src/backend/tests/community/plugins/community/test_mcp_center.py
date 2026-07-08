"""Unit tests for the community ``CommunityMCPCenter`` (B7).

A real, deployable MCP-center impl over a local config-file catalog: empty by
default, allow-all permission, single default tenant. Exercises the empty path
and a populated registry (temp YAML), plus pagination.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.mcp_center import CommunityMCPCenter


def _write_registry(tmp_path, servers: list[dict]) -> str:
    import yaml

    path = tmp_path / "local-mcp-servers.yaml"
    path.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return str(path)


# ── Empty catalog (default) ──────────────────────────────────────────────────

def test_empty_catalog_list_is_success_with_no_data(tmp_path):
    # Point at a non-existent path → empty catalog, not a crash.
    center = CommunityMCPCenter(registry_config_path=str(tmp_path / "absent.yaml"))
    result = center.get_mcp_list()
    assert result["success"] is True
    assert result["data"] == []
    assert result["total"] == 0
    assert result["page_num"] == 1 and result["page_size"] == 20


def test_empty_catalog_detail_is_none(tmp_path):
    center = CommunityMCPCenter(registry_config_path=str(tmp_path / "absent.yaml"))
    assert center.get_mcp_detail("anything") is None


def test_no_configured_path_is_empty_not_repo_fallback():
    # No path ⇒ empty catalog; must NOT fall back to the repo's
    # configs/local-mcp-servers.yaml (a corp/local-dev artifact).
    center = CommunityMCPCenter()
    assert center.get_mcp_list()["total"] == 0
    assert center.get_mcp_detail("anything") is None


# ── Permission is allow-all ──────────────────────────────────────────────────

def test_permission_is_allow_all(tmp_path):
    center = CommunityMCPCenter(registry_config_path=str(tmp_path / "absent.yaml"))
    perm = center.check_mcp_permission_detail("user-1", "mcp.any")
    assert perm["has_permission"] is True
    assert perm["access_level"] == "COMMUNITY"
    assert perm["tool_permissions"] == {}


# ── Default tenant ───────────────────────────────────────────────────────────

def test_tenant_list_returns_single_default(tmp_path):
    center = CommunityMCPCenter(registry_config_path=str(tmp_path / "absent.yaml"))
    result = center.get_tenant_list()
    assert result["success"] is True
    assert len(result["data"]) == 1
    tenant = result["data"][0]
    assert tenant["code"] == "default" and tenant["name"]


# ── Populated registry ───────────────────────────────────────────────────────

def test_populated_catalog_detail_and_list(tmp_path):
    path = _write_registry(
        tmp_path,
        [
            {"serverCode": "mcp.alpha", "name": "Alpha"},
            {"serverCode": "mcp.beta", "name": "Beta"},
        ],
    )
    center = CommunityMCPCenter(registry_config_path=path)

    detail = center.get_mcp_detail("mcp.alpha")
    assert detail is not None and detail["serverCode"] == "mcp.alpha"

    listed = center.get_mcp_list()
    assert listed["total"] == 2
    assert {s["serverCode"] for s in listed["data"]} == {"mcp.alpha", "mcp.beta"}


def test_list_filters_by_server_codes(tmp_path):
    path = _write_registry(
        tmp_path,
        [
            {"serverCode": "mcp.alpha", "name": "Alpha"},
            {"serverCode": "mcp.beta", "name": "Beta"},
        ],
    )
    center = CommunityMCPCenter(registry_config_path=path)
    result = center.get_mcp_list(server_codes=["mcp.beta"])
    assert result["total"] == 1
    assert result["data"][0]["serverCode"] == "mcp.beta"


def test_list_paginates(tmp_path):
    path = _write_registry(
        tmp_path,
        [{"serverCode": f"mcp.s{i}", "name": f"S{i}"} for i in range(5)],
    )
    center = CommunityMCPCenter(registry_config_path=path)
    page1 = center.get_mcp_list(page_num=1, page_size=2)
    page3 = center.get_mcp_list(page_num=3, page_size=2)
    assert page1["total"] == 5 and len(page1["data"]) == 2
    assert len(page3["data"]) == 1  # 5 items, last page has the remainder


def test_list_nonpositive_page_size_returns_all(tmp_path):
    # page_size <= 0 disables pagination: every item comes back in one page.
    path = _write_registry(
        tmp_path,
        [{"serverCode": f"mcp.s{i}", "name": f"S{i}"} for i in range(3)],
    )
    center = CommunityMCPCenter(registry_config_path=path)
    result = center.get_mcp_list(page_size=0)
    assert result["total"] == 3
    assert len(result["data"]) == 3
