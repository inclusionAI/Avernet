"""Rule 25 conformance — AuthPlugin.

Consumer under test: ``GET /api/v1/access/check`` (api/access/router.py:95),
an endpoint that ``Depends(get_current_user)`` and reflects the resolved
``AuthenticatedIdentity.staffId`` back in its log / response shape. The endpoint
is the smallest production caller that proves the full AuthPlugin
contract via FastAPI's dep system, all the way from request to user.

The plugin-hit assertion is observable: the endpoint's behaviour
diverges on whether the local impl extracted an identity. Missing
cookie → 401 (Unauthorized); present cookie → 200 with the parsed
identity surfaced.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext


def test_endpoint_resolves_identity_from_cookie(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get(
        "/api/v1/access/check",
        cookies={"staff_id": "alice"},
    )
    # 200 OK proves the plugin successfully parsed the cookie into a
    # AuthenticatedIdentity that the endpoint accepted. A bypass would 401.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True


def test_endpoint_rejects_request_without_identity(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get("/api/v1/access/check")
    # No cookie → local impl raises Unauthorized → 401 via api-layer mapper.
    # A consumer that bypassed the plugin would never see this status.
    assert resp.status_code == 401


# ── community impl (B4) — OIDC, wired with application.yaml's oidc block ──


@pytest.mark.asyncio
async def test_community_auth_resolves_identity_from_bcs(
    community_world,
) -> None:
    # Proves the community column WIRES OidcAuthPlugin (DI resolution) and that
    # it resolves identity end to end. Config *loading* (the bcs block →
    # application-community.yaml) is covered by the BcsAuthConfig provider tests,
    # so here we give the resolved plugin a known userinfo via its seam rather
    # than standing up a live BCS.
    plugin = community_world.get(AuthPlugin)
    assert type(plugin).__name__ == "OidcAuthPlugin"  # the community impl is wired

    plugin._userinfo_resolver = lambda _hdr: {"user_id": "alice", "name": "Alice"}
    identity = await plugin.resolve_user_from_request(
        AuthRequestContext(cookies={"bcs_session": "tok"})
    )
    assert identity.staffId == "alice"
    assert identity.operatorName == "alice"
