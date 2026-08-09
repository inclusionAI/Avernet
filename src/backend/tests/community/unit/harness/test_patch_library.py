"""Unit tests for PatchLibrary with an in-memory stub repository.

Covers indexing, filtering, and CRUD delegation.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.harness.models import (
    Layer,
    PatchOperation,
    PatchTarget,
    PatchTemplate,
    PatchTemplateStatus,
    RiskLevel,
)
from agentclaw.community.core.repository.protocols.harness import HarnessTemplateRepository
from agentclaw.community.core.harness.services.patch_library import PatchLibrary


class StubRepository(HarnessTemplateRepository):
    """In-memory stub satisfying HarnessTemplateRepository."""

    def __init__(self):
        self._store: dict[int, PatchTemplate] = {}
        self._next_id = 1

    def create(self, tpl: PatchTemplate) -> PatchTemplate:
        tpl.id = self._next_id
        self._store[self._next_id] = tpl
        self._next_id += 1
        return tpl

    def get_by_id(self, tpl_id: int) -> PatchTemplate | None:
        return self._store.get(tpl_id)

    def get_by_name(self, name: str, env: str) -> PatchTemplate | None:
        for t in self._store.values():
            if t.name == name and t.env == env and t.status == PatchTemplateStatus.ACTIVE:
                return t
        return None

    def list(self, layer=None, status=None, keyword=None, offset=0, limit=20):
        items = list(self._store.values())
        if layer:
            items = [t for t in items if str(t.layer) == layer]
        if status:
            items = [t for t in items if str(t.status) == status]
        if keyword:
            items = [t for t in items if keyword in t.name]
        total = len(items)
        return items[offset: offset + limit], total

    def update(self, tpl_id: int, **fields) -> PatchTemplate | None:
        tpl = self._store.get(tpl_id)
        if tpl is None:
            return None
        for k, v in fields.items():
            if k != "version" and v is not None:
                setattr(tpl, k, v)
        tpl.version += 1
        return tpl

    def soft_delete(self, tpl_id: int) -> bool:
        tpl = self._store.get(tpl_id)
        if tpl is None:
            return False
        tpl.status = PatchTemplateStatus.DEPRECATED
        return True

    def load_all_active(self) -> list[PatchTemplate]:
        return [t for t in self._store.values() if t.status == PatchTemplateStatus.ACTIVE]


@pytest.fixture
def lib():
    return PatchLibrary(repo=StubRepository())


def _make_tpl(name, layer=Layer.L1, status=PatchTemplateStatus.ACTIVE,
              skill_sets=None, files=None) -> PatchTemplate:
    applicable = {"skill_sets_contains_any": skill_sets} if skill_sets else None
    return PatchTemplate(
        name=name,
        layer=layer,
        target=PatchTarget(files=files or ["AGENTS.md"]),
        version=1,
        applicable_when=applicable,
        operations=[PatchOperation(op="rewrite_section", target="AGENTS.md")],
        risk_level=RiskLevel.LOW,
        status=status,
        env="dev",
    )


# ── load_all & indices ──────────────────────────────────────


def test_load_all_builds_indices(lib):
    lib.create_template(_make_tpl("a", layer=Layer.L1, files=["A.md"]))
    lib.create_template(_make_tpl("b", layer=Layer.L2, files=["B.md"]))

    assert len(lib._templates) == 2
    assert Layer.L1 in lib._index_by_layer
    assert Layer.L2 in lib._index_by_layer
    assert "A.md" in lib._index_by_target_file
    assert "B.md" in lib._index_by_target_file


# ── create_template ─────────────────────────────────────────


def test_create_template_delegates_and_reloads(lib):
    tpl = _make_tpl("new")
    result = lib.create_template(tpl)
    assert result.id is not None
    assert result.name == "new"


# ── get_template ────────────────────────────────────────────


def test_get_template_by_name(lib):
    lib.create_template(_make_tpl("alpha"))
    assert lib.get_template("alpha", env="dev") is not None
    assert lib.get_template("missing", env="dev") is None


def test_get_template_by_id_string(lib):
    """get_template should support numeric ID string as key."""
    created = lib.create_template(_make_tpl("byid"))
    # Pass ID as string (like "1", "2", "3")
    fetched = lib.get_template(str(created.id), env="dev")
    assert fetched is not None
    assert fetched.name == "byid"


# ── get_template_by_id ──────────────────────────────────────


def test_get_template_by_id_delegates(lib):
    created = lib.create_template(_make_tpl("byid"))
    fetched = lib.get_template_by_id(created.id)
    assert fetched is not None
    assert fetched.name == "byid"


def test_get_template_by_id_missing(lib):
    assert lib.get_template_by_id(999) is None


# ── update_template ─────────────────────────────────────────


def test_update_template_delegates_and_reloads(lib):
    created = lib.create_template(_make_tpl("upd"))
    updated = lib.update_template(created.id, description="changed")
    assert updated is not None
    assert updated.description == "changed"
    # After reload the index-side cached version should be stale, but
    # get_template_by_id hits the repo directly.
    assert lib.get_template_by_id(created.id).description == "changed"


def test_update_missing_returns_none(lib):
    assert lib.update_template(999, description="x") is None


# ── list_templates ──────────────────────────────────────────


def test_list_templates_delegates_and_returns_total(lib):
    for i in range(3):
        lib.create_template(_make_tpl(f"name-{i}"))
    items, total = lib.list_templates(limit=2)
    assert total == 3
    assert len(items) == 2


# ── delete_template ─────────────────────────────────────────


def test_delete_template_delegates_and_reloads(lib):
    created = lib.create_template(_make_tpl("del"))
    assert lib.delete_template(created.id) is True
    # Soft delete → status deprecated, should be gone from active index
    lib.load_all()
    assert lib.get_template("del", env="dev") is None


def test_delete_missing_returns_false(lib):
    assert lib.delete_template(999) is False


# ── list_applicable ─────────────────────────────────────────


def test_list_applicable_filters_by_layer(lib):
    lib.create_template(_make_tpl("l1", layer=Layer.L1))
    lib.create_template(_make_tpl("l2", layer=Layer.L2))
    result = lib.list_applicable(bot_meta={}, layer=Layer.L1)
    assert len(result) == 1
    assert result[0].name == "l1"


def test_list_applicable_skips_deprecated(lib):
    lib.create_template(_make_tpl("active"))
    lib.create_template(_make_tpl("deprecated", status=PatchTemplateStatus.DEPRECATED))
    result = lib.list_applicable(bot_meta={})
    assert len(result) == 1
    assert result[0].name == "active"


def test_list_applicable_filters_by_skill_sets(lib):
    lib.create_template(_make_tpl("match", skill_sets=["sales"]))
    lib.create_template(_make_tpl("nomatch", skill_sets=["hr"]))
    result = lib.list_applicable(bot_meta={"skill_sets": ["sales"]})
    assert len(result) == 1
    assert result[0].name == "match"


def test_list_applicable_no_skill_condition_matches_all(lib):
    lib.create_template(_make_tpl("free"))
    result = lib.list_applicable(bot_meta={"skill_sets": []})
    assert len(result) == 1


# ── validate ────────────────────────────────────────────────


def test_validate_fails_on_empty_name(lib):
    from agentclaw.community.core.harness.models import PatchDefinition
    empty_name = PatchDefinition(name="", template_id=1, layer=Layer.L1)
    errors = lib.validate(empty_name)
    assert "name is required" in " ".join(errors).lower()


def test_validate_fails_on_bad_scope(lib):
    from agentclaw.community.core.harness.models import PatchDefinition
    bad_scope = PatchDefinition(name="x", template_id=1, layer=Layer.L1, scope="bad")
    errors = lib.validate(bad_scope)
    assert "scope" in " ".join(errors).lower()


def test_validate_empty_on_good(lib):
    from agentclaw.community.core.harness.models import PatchDefinition
    ok = PatchDefinition(name="x", template_id=1, layer=Layer.L1)
    assert lib.validate(ok) == []
