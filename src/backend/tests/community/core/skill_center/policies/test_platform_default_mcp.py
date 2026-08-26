from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.policies import platform_default_mcp
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)


def test_resolves_defaults_from_the_exact_bot_context(monkeypatch) -> None:
    calls: list[dict] = []

    def resolve(engine_type, template_type, *, ext_info):
        calls.append(
            {
                "engine_type": engine_type,
                "template_type": template_type,
                "ext_info": ext_info,
            }
        )
        return ["mcp.platform", "mcp.template"]

    monkeypatch.setattr(
        platform_default_mcp,
        "get_default_mcp_server_codes",
        resolve,
    )
    policy = PlatformDefaultMcpPolicy(
        lambda bot_id: {"template_config": {"bot_id": bot_id}}
    )

    assert policy.server_codes_for(
        {
            "bot_id": "bot-1",
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }
    ) == frozenset({"mcp.platform", "mcp.template"})
    assert calls == [
        {
            "engine_type": "claude_code",
            "template_type": "personalCoding",
            "ext_info": {"template_config": {"bot_id": "bot-1"}},
        }
    ]


def test_policy_context_failure_propagates() -> None:
    def fail(_bot_id: str):
        raise RuntimeError("template service unavailable")

    policy = PlatformDefaultMcpPolicy(fail)

    with pytest.raises(RuntimeError, match="template service unavailable"):
        policy.server_codes_for(
            {
                "bot_id": "bot-1",
                "active_engine": "claude_code",
                "template_type": "personalCoding",
            }
        )
