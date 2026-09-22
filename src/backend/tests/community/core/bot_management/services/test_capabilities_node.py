"""Unit tests for capabilities_node — the key-presence terminal contract."""
from __future__ import annotations

from agentclaw.community.core.bot_management.capabilities import (
    capabilities_node,
    has_declared_capabilities,
)


class TestCapabilitiesNode:
    """capabilities_node: 键存在即唯一事实源，值畸形按全关闭。"""

    def test_none_when_key_absent(self):
        assert capabilities_node({"bot_template_config": {}}) is None
        assert capabilities_node({}) is None
        assert capabilities_node(None) is None
        assert capabilities_node("string") is None

    def test_node_returned_when_declared_mapping(self):
        caps = {"dima_workspace": True}
        assert capabilities_node({"capabilities": caps}) == caps

    def test_empty_dict_when_declared_but_not_mapping(self):
        """键存在但值畸形 → {}（全部能力关闭），不返回 None。"""
        assert capabilities_node({"capabilities": None}) == {}
        assert capabilities_node({"capabilities": "oops"}) == {}
        assert capabilities_node({"capabilities": ["a", "b"]}) == {}

    def test_has_declared_capabilities_is_key_presence_only(self):
        assert has_declared_capabilities({"capabilities": None}) is True
        assert has_declared_capabilities({}) is False