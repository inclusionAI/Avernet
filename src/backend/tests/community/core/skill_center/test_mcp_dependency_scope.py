"""The single decoder for a Skill's declared MCP dependencies.

Two readers of the same column must agree exactly: the projection resolver,
which folds a Skill's dependencies into the MCP set it delivers, and the
command that scopes a Skill mutation, which has to name the codes that
projection will resolve. A mutation that scoped a different set than it
projects would leave a dependency whitelisted but never configured, or delete
one still in use.

These pin the shapes the stored column actually holds, plus the reader that
turns one ``ac_skill`` row into codes.
"""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)


class TestStoredShapes:
    """The column is historical and mixed; every live shape must decode."""

    def test_bare_codes(self):
        assert mcp_dependency_codes(["mcp.a", "mcp.b"]) == ("mcp.a", "mcp.b")

    def test_server_code_objects(self):
        assert mcp_dependency_codes(
            [{"server_code": "mcp.a"}, {"server_code": "mcp.b"}]
        ) == ("mcp.a", "mcp.b")

    def test_legacy_code_key(self):
        assert mcp_dependency_codes([{"code": "mcp.a"}]) == ("mcp.a",)

    def test_server_code_wins_over_code(self):
        assert mcp_dependency_codes(
            [{"server_code": "mcp.new", "code": "mcp.old"}]
        ) == ("mcp.new",)

    def test_mixed_shapes_in_one_row(self):
        assert mcp_dependency_codes(
            ["mcp.a", {"server_code": "mcp.b"}, {"code": "mcp.c"}]
        ) == ("mcp.a", "mcp.b", "mcp.c")

    def test_no_dependencies(self):
        assert mcp_dependency_codes([]) == ()

    def test_order_and_duplicates_are_preserved(self):
        """Callers collect into a set; this one does not decide dedup for them."""
        assert mcp_dependency_codes(["b", "a", "b"]) == ("b", "a", "b")


class TestUnrecognisedShapes:
    """A dropped dependency is an MCP the Skill needs and never receives."""

    @pytest.mark.parametrize(
        "dependency", [None, 7, ["nested"], {}, {"name": "mcp.a"}, {"code": 7}]
    )
    def test_an_unrecognised_entry_raises_rather_than_being_skipped(self, dependency):
        with pytest.raises(ValueError):
            mcp_dependency_codes([dependency])

    def test_one_bad_entry_rejects_the_whole_row(self):
        with pytest.raises(ValueError):
            mcp_dependency_codes(["mcp.a", None])


class TestRowReader:
    """``ac_skill.mcp_dependencies`` is stored as a JSON string."""

    def test_a_json_string_column(self):
        row = SimpleNamespace(mcp_dependencies='["mcp.a", {"server_code": "mcp.b"}]')
        assert skill_mcp_dependency_codes(row) == frozenset({"mcp.a", "mcp.b"})

    def test_an_already_decoded_column(self):
        row = SimpleNamespace(mcp_dependencies=["mcp.a"])
        assert skill_mcp_dependency_codes(row) == frozenset({"mcp.a"})

    @pytest.mark.parametrize("empty", [None, "", "[]", []])
    def test_an_empty_column_declares_nothing(self, empty):
        assert skill_mcp_dependency_codes(SimpleNamespace(mcp_dependencies=empty)) == (
            frozenset()
        )

    def test_a_missing_row_declares_nothing(self):
        """The Skill may already be gone; that is not a dependency claim."""
        assert skill_mcp_dependency_codes(None) == frozenset()

    def test_malformed_json_raises_rather_than_reading_as_empty(self):
        """The projection decodes the same column moments later and would fail
        on it too; failing here rolls the mutation back instead of leaving
        desired state and runtime disagreeing."""
        row = SimpleNamespace(mcp_dependencies="{not json")
        with pytest.raises(ValueError):
            skill_mcp_dependency_codes(row)
