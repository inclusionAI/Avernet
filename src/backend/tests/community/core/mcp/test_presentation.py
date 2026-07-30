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
    normalize_network_types,
    primary_transport_protocol,
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

    def test_singular_disallowed_type_is_not_visible(self):
        # The local registry filters its list on the singular networkType /
        # network_type key; the detail check must honor the same shape or a
        # server hidden from the list would still resolve by code.
        assert is_network_type_visible({"networkType": "INTRANET"}) is False
        assert is_network_type_visible({"network_type": "INTRANET"}) is False
        assert is_network_type_visible({"networkType": ["INTRANET"]}) is False

    def test_singular_allowed_type_is_visible(self):
        assert is_network_type_visible({"networkType": "INTERNET"}) is True
        assert is_network_type_visible({"network_type": "OFFICE"}) is True

    def test_plural_takes_precedence_over_singular(self):
        # A present plural list is authoritative — the singular fallback only
        # fires when networkTypes is absent/empty, so existing behavior is intact.
        assert is_network_type_visible(
            {"networkTypes": ["OFFICE"], "networkType": "INTRANET"}
        ) is True

    def test_allowlist_is_internet_and_office(self):
        assert ALLOWED_NETWORK_TYPES == ("INTERNET", "OFFICE")


class TestNormalizeNetworkTypes:
    def test_plural_list_returned_as_is(self):
        assert normalize_network_types({"networkTypes": ["INTERNET", "OFFICE"]}) == [
            "INTERNET",
            "OFFICE",
        ]

    def test_singular_scalar_wrapped(self):
        assert normalize_network_types({"networkType": "INTRANET"}) == ["INTRANET"]
        assert normalize_network_types({"network_type": "OFFICE"}) == ["OFFICE"]

    def test_singular_list_returned(self):
        assert normalize_network_types({"networkType": ["INTERNET"]}) == ["INTERNET"]

    def test_plural_takes_precedence_over_singular(self):
        assert normalize_network_types(
            {"networkTypes": ["OFFICE"], "networkType": "INTRANET"}
        ) == ["OFFICE"]

    def test_absent_or_empty_is_empty_list(self):
        assert normalize_network_types({}) == []
        assert normalize_network_types({"networkTypes": []}) == []

    def test_non_string_members_dropped(self):
        assert normalize_network_types({"networkTypes": ["OFFICE", None, 3]}) == ["OFFICE"]


class TestPrimaryTransportProtocol:
    def test_read_from_endpoint_not_top_level(self):
        # Real MCP Center records carry transportProtocol per endpoint.
        data = {
            "endpoints": [
                {"env": "PRE", "networkType": "INTERNET",
                 "transportProtocol": "STREAMABLE_HTTP", "url": "https://x"}
            ]
        }
        assert primary_transport_protocol(data) == "STREAMABLE_HTTP"

    def test_prefers_allowed_network_endpoint(self):
        data = {
            "endpoints": [
                {"networkType": "INTRANET", "transportProtocol": "SSE"},
                {"networkType": "INTERNET", "transportProtocol": "STREAMABLE_HTTP"},
            ]
        }
        assert primary_transport_protocol(data) == "STREAMABLE_HTTP"

    def test_falls_back_to_any_endpoint_with_protocol(self):
        data = {"endpoints": [{"networkType": "INTRANET", "transportProtocol": "SSE"}]}
        assert primary_transport_protocol(data) == "SSE"

    def test_falls_back_to_top_level_when_no_endpoints(self):
        assert primary_transport_protocol({"transportProtocol": "SSE"}) == "SSE"

    def test_none_when_absent_everywhere(self):
        assert primary_transport_protocol({}) is None
        assert primary_transport_protocol({"endpoints": [{"url": "https://x"}]}) is None
