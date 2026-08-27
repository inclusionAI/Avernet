"""``unpack_resource_scope`` — the guard on the Passport MCP identity boundary.

``updatePassport`` replaces each resource list wholesale, and the Passport port
fills a missing ``identity_mode`` with ``"owner"``. A scope that grants MCPs
without saying under whose identity each one runs therefore does not leave
identity alone: it asserts Owner for every MCP and discards the bot's caller
grants. These tests pin that such a scope is refused here, at the one seam
every caller passes through, rather than each caller being trusted to remember.
"""

import pytest

from agentclaw.community.plugin_api.passport import unpack_resource_scope


class TestNonResourceUpdates:
    """Admin/metadata updates must keep leaving existing grants untouched."""

    def test_none_scope_is_a_non_resource_update(self):
        assert unpack_resource_scope(None) == (None, None)


class TestIdentityBearingScope:
    """``mcp_items`` is the shape that carries identity, so it passes through."""

    def test_items_are_returned_verbatim(self):
        items = [
            {"mcp_code": "a", "identity_mode": "caller"},
            {"mcp_code": "b", "identity_mode": "owner"},
        ]
        mcp, cli = unpack_resource_scope(
            {"mcp_items": items, "cli_items": [{"cli_code": "c"}]}
        )
        assert mcp == items
        assert cli == [{"cli_code": "c"}]

    def test_items_win_over_codes(self):
        """``mcp_codes`` is ignored when items are present — one source only."""
        mcp, _ = unpack_resource_scope({
            "mcp_codes": ["stale"],
            "mcp_items": [{"mcp_code": "fresh", "identity_mode": "caller"}],
            "cli_items": [],
        })
        assert mcp == [{"mcp_code": "fresh", "identity_mode": "caller"}]

    def test_items_alone_do_not_require_codes(self):
        mcp, cli = unpack_resource_scope({"mcp_items": [], "cli_items": []})
        assert (mcp, cli) == ([], [])


class TestCodeOnlyScope:
    """The regression: a code-only grant is a silent demotion, so it is refused."""

    def test_non_empty_codes_without_items_are_rejected(self):
        with pytest.raises(ValueError) as excinfo:
            unpack_resource_scope({"mcp_codes": ["a", "b"], "cli_items": []})
        # The message must name the fix, not just the rule.
        assert "mcp_items" in str(excinfo.value)

    def test_a_single_code_is_enough_to_reject(self):
        with pytest.raises(ValueError):
            unpack_resource_scope({"mcp_codes": ["only"], "cli_items": []})

    def test_empty_codes_stay_legal(self):
        """An empty grant has no identity to lose, so clearing MCP scope needs
        no items — otherwise a caller with nothing to grant would have to
        assemble an identity lookup purely to satisfy the guard."""
        mcp, cli = unpack_resource_scope(
            {"mcp_codes": [], "cli_items": [{"cli_code": "c"}]}
        )
        assert mcp == []
        assert cli == [{"cli_code": "c"}]


class TestMalformedScope:
    """Both lists are required: one resource type must not clear the other."""

    def test_missing_cli_items_is_rejected(self):
        with pytest.raises(ValueError):
            unpack_resource_scope({"mcp_codes": []})

    def test_missing_cli_items_is_rejected_even_with_mcp_items(self):
        with pytest.raises(ValueError):
            unpack_resource_scope({"mcp_items": [{"mcp_code": "a"}]})

    def test_missing_both_mcp_keys_is_rejected(self):
        with pytest.raises(ValueError):
            unpack_resource_scope({"cli_items": []})
