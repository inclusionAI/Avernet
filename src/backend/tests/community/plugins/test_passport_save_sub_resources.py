"""Coverage for the B11 Phase-A ``PassportPlugin.save_sub_resources`` seam.

Resources yuque-permission sync now routes through the injected
``PassportPlugin``; the community (``SelfIssuedPassportPlugin``) and local
(``LocalPassportPlugin``) impls are no-ops that must return ``True``.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.passport import SubResourceItem
from agentclaw.community.plugins.community.passport import SelfIssuedPassportPlugin
from agentclaw.community.plugins.local.passport import LocalPassportPlugin


def _one_sub_resource() -> list[SubResourceItem]:
    return [
        SubResourceItem(
            resource_type="MCP_TOOL",
            sub_resource_type="YUQUE_DOC",
            sub_resource_code="https://yuque/doc/1",
            detail_config={"access_modes": ["READ"]},
        )
    ]


def test_community_passport_save_sub_resources_is_noop_true():
    plugin = SelfIssuedPassportPlugin()
    assert plugin.save_sub_resources("bot-1", "user-1", _one_sub_resource()) is True


def test_local_passport_save_sub_resources_is_noop_true():
    plugin = LocalPassportPlugin()
    assert plugin.save_sub_resources("bot-1", "user-1", _one_sub_resource()) is True
