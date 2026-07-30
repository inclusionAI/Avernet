"""Unit tests for the shared MCP presentation helpers.

These rules were duplicated inside the internal ``/api/mcp`` router; they are
tested here as pure units so the two API surfaces provably apply the same
masking, ``extInfo`` stripping, and network-type visibility.
"""

from __future__ import annotations

from agentclaw.community.core.mcp.presentation import (
    ALLOWED_NETWORK_TYPES,
    is_network_type_visible,
    mask_api_key,
    strip_ext_info,
    strip_ext_info_from_list,
)


class TestMaskApiKey:
    def test_long_key_keeps_first_and_last_four(self):
        assert mask_api_key("abcdefghijkl") == "abcd****ijkl"

    def test_boundary_length_eight_is_fully_masked(self):
        # len == 8 is not > 8, so it is fully masked (reveal never exceeds hide).
        assert mask_api_key("abcdefgh") == "****"

    def test_short_key_is_fully_masked(self):
        assert mask_api_key("abc") == "****"

    def test_none_stays_none(self):
        assert mask_api_key(None) is None

    def test_empty_string_is_none(self):
        # Falsy key: "no key", not "masked key".
        assert mask_api_key("") is None


class TestStripExtInfo:
    def test_ext_info_removed_from_tool_properties(self):
        data = {
            "tools": [
                {"inputSchema": {"properties": {"extInfo": {"x": 1}, "keep": 2}}}
            ]
        }
        out = strip_ext_info(data)
        props = out["tools"][0]["inputSchema"]["properties"]
        assert "extInfo" not in props
        assert props["keep"] == 2

    def test_absent_ext_info_leaves_properties_intact(self):
        data = {"tools": [{"inputSchema": {"properties": {"keep": 2}}}]}
        out = strip_ext_info(data)
        assert out["tools"][0]["inputSchema"]["properties"] == {"keep": 2}

    def test_input_is_not_mutated(self):
        data = {"tools": [{"inputSchema": {"properties": {"extInfo": 1}}}]}
        strip_ext_info(data)
        assert "extInfo" in data["tools"][0]["inputSchema"]["properties"]

    def test_non_dict_shapes_are_tolerated(self):
        assert strip_ext_info({"tools": "not-a-list"}) == {"tools": "not-a-list"}
        assert strip_ext_info({"tools": ["not-a-dict"]}) == {"tools": ["not-a-dict"]}
        assert strip_ext_info({}) == {}

    def test_list_variant_strips_each_item(self):
        result = {
            "total": 2,
            "data": [
                {"tools": [{"inputSchema": {"properties": {"extInfo": 1, "a": 2}}}]},
                {"tools": [{"inputSchema": {"properties": {"extInfo": 3, "b": 4}}}]},
            ],
        }
        out = strip_ext_info_from_list(result)
        assert out["total"] == 2
        for item in out["data"]:
            props = item["tools"][0]["inputSchema"]["properties"]
            assert "extInfo" not in props


class TestNetworkTypeVisibility:
    def test_allowed_type_is_visible(self):
        assert is_network_type_visible({"networkTypes": ["INTERNET"]}) is True
        assert is_network_type_visible({"networkTypes": ["OFFICE"]}) is True

    def test_disallowed_type_only_is_not_visible(self):
        assert is_network_type_visible({"networkTypes": ["SECRET"]}) is False

    def test_mixed_types_visible_if_any_allowed(self):
        assert is_network_type_visible({"networkTypes": ["SECRET", "OFFICE"]}) is True

    def test_empty_or_absent_is_visible(self):
        assert is_network_type_visible({"networkTypes": []}) is True
        assert is_network_type_visible({}) is True

    def test_allowlist_is_internet_and_office(self):
        assert ALLOWED_NETWORK_TYPES == ("INTERNET", "OFFICE")
