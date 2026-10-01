"""Route-B acceptance: MCP facade baseline contract on live backend.

The market/tenant endpoints are remote-only and return empty local lists. The
permission check uses the explicit ``SINGLEBOX_ACCEPTANCE_MCP_CENTER`` fixture
for ``mcp.singlebox.*`` servers, then the local auth client returns AUTHORIZED.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


BASELINE_PATH = Path(__file__).parent / "baseline_mcp_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_mcp_query_baseline_live(live_backend, acceptance_fs_root):
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        market = client.get("/api/mcp/market/list")
        tenants = client.get("/api/mcp/tenants")
        permission = client.get(
            "/api/mcp/market/permission",
            params={"server_code": "mcp.singlebox.acceptance", "user_id": "e2e_user"},
        )

    assert market.status_code == 200, market.text
    assert tenants.status_code == 200, tenants.text
    assert permission.status_code == 200, permission.text

    market_body = market.json()
    tenants_body = tenants.json()
    permission_body = permission.json()
    snapshot = {
        "market_list": {
            "success": market_body["success"],
            "entries_count": len(market_body["data"]),
        },
        "tenants": {
            "success": tenants_body["success"],
            "entries_count": len(tenants_body["data"]),
        },
        "permission_fixture_authorized": {
            "success": permission_body["success"],
            "has_permission": permission_body["has_permission"],
        },
    }
    assert snapshot == json.loads(BASELINE_PATH.read_text())
