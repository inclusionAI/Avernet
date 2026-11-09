"""Unit tests for unexpected session-lock HTTP failures."""

import logging
from unittest.mock import Mock

import pytest


async def _invoke_session_lock_route(action, service):
    from types import SimpleNamespace
    from importlib import import_module
    module = import_module("agentclaw.community.adapters.http.bot_collaborator.session_lock_router")
    from agentclaw.community.adapters.http.bot_collaborator.schemas import (
        SessionLockRequest,
        SessionReleaseLockRequest,
    )

    user = SimpleNamespace(staffId="u_owner")
    values = {"bot_id": "app_test", "owner_id": "u_owner", "session_id": "sess_001"}
    if action == "info":
        return await module.get_session_lock_info(**values, user=user, service=service)
    request_cls = SessionReleaseLockRequest if action == "release" else SessionLockRequest
    handler = getattr(module, f"{action}_session_lock")
    return await handler(request=request_cls(**values), user=user, service=service)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["acquire", "release", "steal", "info"])
async def test_session_lock_unexpected_error_remains_server_error(action, caplog):
    service = Mock()
    method = "get_session_lock_info" if action == "info" else f"{action}_session_lock"
    getattr(service, method).side_effect = RuntimeError("database unavailable")
    with caplog.at_level(logging.ERROR):
        result = await _invoke_session_lock_route(action, service)
    assert result.success is False
    assert result.error_code == 500
    assert result.data is None
    assert "database unavailable" in caplog.text
