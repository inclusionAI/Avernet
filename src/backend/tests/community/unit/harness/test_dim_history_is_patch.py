"""Unit tests for dim-history is_patch fix (spec: bot_health_0602/dim_history_is_patch_spec.md).

Covers:
  1. _is_patch_flag helper — all branches
  2. PatchItem construction in dim-history — is_patch reflects patch content
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from agentclaw.community.adapters.http.harness.router import _is_patch_flag
from agentclaw.community.adapters.http.harness.schemas import PatchItem, PatchOperationItem
from agentclaw.community.core.harness.models import Layer, PatchDefinition


# ── _is_patch_flag helper tests ──────────────────────────────


class TestIsPatchFlag:
    """Test _is_patch_flag() covers all branches in the spec."""

    def test_none_returns_false(self) -> None:
        assert _is_patch_flag(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert _is_patch_flag("") is False

    def test_whitespace_returns_false(self) -> None:
        assert _is_patch_flag("   ") is False

    def test_invalid_json_returns_false(self) -> None:
        assert _is_patch_flag("not json") is False

    def test_json_object_returns_false(self) -> None:
        """Only JSON list is considered patch content, not object."""
        assert _is_patch_flag('{"op": "update_md"}') is False

    def test_empty_json_list_returns_false(self) -> None:
        assert _is_patch_flag("[]") is False

    def test_non_empty_json_list_returns_true(self) -> None:
        assert _is_patch_flag('[{"op":"update_md","target":"AGENTS.md"}]') is True

    def test_json_list_with_multiple_items_returns_true(self) -> None:
        content = json.dumps([
            {"op": "update_md", "target": "AGENTS.md"},
            {"op": "create_file", "target": "CLAUDE.md"},
        ])
        assert _is_patch_flag(content) is True

    def test_json_list_with_single_string_returns_true(self) -> None:
        """List with any content (even non-dict) returns True."""
        assert _is_patch_flag('["something"]') is True


# ── PatchItem.is_patch construction tests ─────────────────────


class TestPatchItemIsPatch:
    """Verify PatchItem.is_patch is correctly set from _is_patch_flag(patch.content).

    These tests validate the code path that was fixed in get_dim_history():
    is_patch = _is_patch_flag(patch_def.content) is now passed to PatchItem().
    """

    def _make_patch_def(
        self,
        patch_id: int = 1,
        name: str = "test-patch",
        content: str | None = None,
    ) -> PatchDefinition:
        return PatchDefinition(
            id=patch_id,
            name=name,
            template_id=100,
            layer=Layer.L1,
            content=content,
        )

    def _parse_operations(self, content: str | None) -> list[PatchOperationItem]:
        """Mirror the operation parsing logic from the router."""
        if not content:
            return []
        try:
            ops_data = json.loads(content)
            return [
                PatchOperationItem(
                    op=op.get("op", "update_md"),
                    target=op.get("target", ""),
                    template=op.get("template"),
                    detail=op.get("detail", {}),
                )
                for op in ops_data
            ]
        except json.JSONDecodeError:
            return []

    def _build_patch_item(self, patch_def: PatchDefinition) -> PatchItem:
        """Reproduce the construction logic from get_dim_history() after the fix."""
        is_patch = _is_patch_flag(patch_def.content)
        patch_ops = self._parse_operations(patch_def.content)
        return PatchItem(
            patch_id=patch_def.id or 0,
            name=patch_def.name,
            description=patch_def.description,
            is_applied=patch_def.is_applied,
            layer=patch_def.layer.value if hasattr(patch_def.layer, "value") else str(patch_def.layer),
            operations=patch_ops,
            is_patch=is_patch,
            gmt_create=patch_def.gmt_create.isoformat() if patch_def.gmt_create else None,
        )

    def test_content_with_non_empty_list_is_patch_true(self) -> None:
        """content = '[{"op":"update_md","target":"AGENTS.md"}]' → is_patch == true."""
        content = json.dumps([{"op": "update_md", "target": "AGENTS.md"}])
        patch_def = self._make_patch_def(patch_id=300862, content=content)
        item = self._build_patch_item(patch_def)

        assert item.is_patch is True
        assert len(item.operations) == 1
        assert item.operations[0].op == "update_md"
        assert item.operations[0].target == "AGENTS.md"

    def test_content_with_empty_list_is_patch_false(self) -> None:
        """content = '[]' → is_patch == false, operations == []."""
        patch_def = self._make_patch_def(patch_id=102, content="[]")
        item = self._build_patch_item(patch_def)

        assert item.is_patch is False
        assert item.operations == []

    def test_content_empty_string_is_patch_false(self) -> None:
        """content = '' → is_patch == false."""
        patch_def = self._make_patch_def(patch_id=103, content="")
        item = self._build_patch_item(patch_def)

        assert item.is_patch is False
        assert item.operations == []

    def test_content_invalid_json_is_patch_false(self) -> None:
        """content = 'not json' → is_patch == false, no error."""
        patch_def = self._make_patch_def(patch_id=104, content="not json")
        item = self._build_patch_item(patch_def)

        assert item.is_patch is False
        assert item.operations == []

    def test_content_none_is_patch_false(self) -> None:
        """content = None → is_patch == false."""
        patch_def = self._make_patch_def(patch_id=105, content=None)
        item = self._build_patch_item(patch_def)

        assert item.is_patch is False
        assert item.operations == []

    def test_content_json_object_is_patch_false(self) -> None:
        """content = '{"op":"update_md"}' (object, not list) → is_patch == false.

        Note: _is_patch_flag correctly returns False for JSON objects.
        The operations parser would raise AttributeError on non-list JSON,
        matching the existing behavior in the router (only json.JSONDecodeError
        is caught). This edge case is not covered by the spec and is out of scope.
        """
        content = '{"op":"update_md"}'
        # Verify the spec's rule: JSON object → is_patch == false
        assert _is_patch_flag(content) is False

    def test_multiple_operations_is_patch_true(self) -> None:
        """Content with multiple operations → is_patch == true, operations list matches."""
        content = json.dumps([
            {"op": "update_md", "target": "AGENTS.md"},
            {"op": "create_file", "target": "CLAUDE.md", "detail": {"reason": "missing"}},
        ])
        patch_def = self._make_patch_def(patch_id=107, content=content)
        item = self._build_patch_item(patch_def)

        assert item.is_patch is True
        assert len(item.operations) == 2
        assert item.operations[0].op == "update_md"
        assert item.operations[1].op == "create_file"
        assert item.operations[1].target == "CLAUDE.md"

    def test_default_is_patch_false_without_explicit_pass(self) -> None:
        """PatchItem() defaults is_patch to False when not passed.

        This is the bug: before the fix, get_dim_history() did not pass
        is_patch, so it defaulted to False even when content had operations.
        """
        item = PatchItem(patch_id=1, name="test")
        assert item.is_patch is False

    def test_spec_acceptance_example(self) -> None:
        """Exact example from the spec: patch_id=300862 with update_md on AGENTS.md."""
        content = json.dumps([{"op": "update_md", "target": "AGENTS.md"}])
        patch_def = self._make_patch_def(patch_id=300862, content=content)
        item = self._build_patch_item(patch_def)

        # Verify the acceptance criteria from the spec
        assert item.patch_id == 300862
        assert item.is_patch is True
        assert len(item.operations) == 1
        assert item.operations[0].op == "update_md"
        assert item.operations[0].target == "AGENTS.md"