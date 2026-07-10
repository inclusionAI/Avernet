from __future__ import annotations

import pytest

from agentclaw.community.di.modules.singlebox_access_module import (
    SingleboxAccessModule,
)
from agentclaw.community.plugins.local.policy_service import LocalPolicyService


@pytest.mark.parametrize("server_env", [None, "dev", "pre", "prod"])
def test_singlebox_access_always_returns_local_policy(monkeypatch, server_env):
    if server_env is None:
        monkeypatch.delenv("SERVER_ENV", raising=False)
    else:
        monkeypatch.setenv("SERVER_ENV", server_env)

    result = SingleboxAccessModule()._policy_service_protocol()

    assert isinstance(result, LocalPolicyService)
