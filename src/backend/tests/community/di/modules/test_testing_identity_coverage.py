"""Singlebox coverage instrumentation for the local AuthPlugin provider."""
from __future__ import annotations

import json

import pytest

from agentclaw.community.di.modules.infrastructure.test.identity import (
    TestIdentityModule,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


@pytest.mark.asyncio
async def test_auth_provider_records_runtime_reachable_plugin_methods(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    auth = TestIdentityModule().auth()
    context = AuthRequestContext(headers={"x-user-id": "auth_e2e_user"})

    await auth.resolve_user_from_request(context)
    auth.is_operator_allowed("auth_e2e_user")
    await auth.authorize_entity_access(
        context,
        requested_entity_id="auth_e2e_user",
        requested_entity_type="staff",
    )

    hit_path = tmp_path / "backend" / "plugin_hits.jsonl"
    keys = {
        json.loads(line)["key"]
        for line in hit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert keys == {
        "AuthPlugin.resolve_user_from_request",
        "AuthPlugin.is_operator_allowed",
        "AuthPlugin.authorize_entity_access",
    }
