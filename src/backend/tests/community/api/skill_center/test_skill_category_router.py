"""Tests for skill_category API router."""
import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.skill_center.skill_category import (
    router as category_router,
    _build_path,
    _build_tree,
    _to_tree_node,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillCategoryRepository,
    SkillRepository,
)


def _bind_repos(category_repo, skill_repo=None):
    """Bind repos for DI in tests. Falls back to MagicMock for SkillRepository
    when a route uses Injected(SkillRepository) but the test doesn't care."""
    class _M(Module):
        def configure(self, binder):
            binder.bind(SkillCategoryRepository, to=category_repo)
            binder.bind(SkillRepository, to=skill_repo if skill_repo is not None else MagicMock())
    return _M()


def _attach(app, category_repo, skill_repo=None):
    attach_injector(app, Injector([_bind_repos(category_repo, skill_repo)]))


# ── Fixtures ────────────────────────────────────────────────────────────────

def _make_category(**overrides):
    """Build a category dict with sensible defaults."""
    code = overrides.get("code", "root")
    base = {
        "id": "1",
        "code": code,
        "name": code.capitalize(),
        "parent_code": "",
        "path": f"/{code}/",
        "level": 0,
        "sort_order": 0,
        "status": 1,
        "gmt_created": "2025-01-01T00:00:00",
        "gmt_modified": "2025-01-01T00:00:00",
    }
    base.update(overrides)
    # If path not explicitly overridden, derive from code and parent_code
    if "path" not in overrides:
        parent_code = base["parent_code"]
        if parent_code:
            base["path"] = f"/{parent_code}/{code}/"
            base["level"] = 1
        else:
            base["path"] = f"/{code}/"
            base["level"] = 0
    return base


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.list_active.return_value = []
    repo.get_by_code.return_value = None
    repo.get_by_path.return_value = None
    repo.update_by_path.return_value = None
    repo.create.return_value = _make_category()
    repo.update.return_value = _make_category()
    repo.list_descendant_codes.return_value = []
    return repo


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(category_router)
    tc = TestClient(app, raise_server_exceptions=False)
    tc._app = app  # expose for tests that need to attach the injector
    return tc


# ── Helper function tests ───────────────────────────────────────────────────

class TestBuildPath:
    def test_root_category(self):
        repo = MagicMock()
        path, level = _build_path("", "business", repo)
        assert path == "/business/"
        assert level == 0

    def test_child_category(self):
        repo = MagicMock()
        repo.get_by_code.return_value = {"path": "/business/", "level": 0}
        path, level = _build_path("business", "aml", repo)
        assert path == "/business/aml/"
        assert level == 1

    def test_parent_not_found_raises(self):
        repo = MagicMock()
        repo.get_by_code.return_value = None
        with pytest.raises(ValueError, match="Parent category 'missing' not found"):
            _build_path("missing", "child", repo)


class TestBuildTree:
    def test_empty_list(self):
        assert _build_tree([]) == []

    def test_flat_roots(self):
        cats = [
            _make_category(code="a", parent_code=""),
            _make_category(code="b", parent_code=""),
        ]
        roots = _build_tree(cats)
        assert len(roots) == 2
        assert roots[0]["code"] == "a"
        assert roots[1]["code"] == "b"

    def test_nested_tree(self):
        cats = [
            _make_category(code="root", parent_code="", path="/root/"),
            _make_category(code="child1", parent_code="root", path="/root/child1/"),
            _make_category(code="child2", parent_code="root", path="/root/child2/"),
            _make_category(code="grandchild", parent_code="child1", path="/root/child1/grandchild/"),
        ]
        roots = _build_tree(cats)
        assert len(roots) == 1
        assert roots[0]["code"] == "root"
        assert len(roots[0]["children"]) == 2
        assert roots[0]["children"][0]["code"] == "child1"
        assert len(roots[0]["children"][0]["children"]) == 1
        assert roots[0]["children"][0]["children"][0]["code"] == "grandchild"

    def test_orphan_becomes_root(self):
        cats = [
            _make_category(code="orphan", parent_code="nonexistent"),
        ]
        roots = _build_tree(cats)
        assert len(roots) == 1
        assert roots[0]["code"] == "orphan"


class TestToTreeNode:
    def test_basic_conversion(self):
        node = _make_category(code="x", name="X", parent_code="p", level=1, sort_order=5)
        result = _to_tree_node(node)
        assert result.code == "x"
        assert result.name == "X"
        assert result.parent_code == "p"
        assert result.level == 1
        assert result.sort_order == 5
        assert result.children == []

    def test_recursive_children(self):
        node = {
            "code": "parent",
            "name": "Parent",
            "parent_code": "",
            "level": 0,
            "sort_order": 0,
            "status": 1,
            "children": [
                {
                    "code": "child",
                    "name": "Child",
                    "parent_code": "parent",
                    "level": 1,
                    "sort_order": 0,
                    "status": 1,
                    "children": [],
                },
            ],
        }
        result = _to_tree_node(node)
        assert result.code == "parent"
        assert len(result.children) == 1
        assert result.children[0].code == "child"

    def test_defaults_for_missing_fields(self):
        node = {"code": "minimal", "name": "Minimal"}
        result = _to_tree_node(node)
        assert result.parent_code == ""
        assert result.level == 0
        assert result.sort_order == 0
        assert result.status == 1
        assert result.children == []


# ── GET /tree ───────────────────────────────────────────────────────────────

class TestGetCategoryTree:
    def test_success(self, client, mock_repo):
        mock_repo.list_active.return_value = [
            _make_category(code="root", parent_code=""),
            _make_category(code="child", parent_code="root", level=1, path="/root/child/"),
        ]
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "root"
        assert len(data["data"][0]["children"]) == 1

    def test_empty(self, client, mock_repo):
        mock_repo.list_active.return_value = []
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories/tree")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ── GET / (list) ────────────────────────────────────────────────────────────

class TestListCategories:
    def test_list_all(self, client, mock_repo):
        mock_repo.list_active.return_value = [
            _make_category(code="a"),
            _make_category(code="b"),
        ]
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_filter_by_parent_code(self, client, mock_repo):
        mock_repo.list_active.return_value = [
            _make_category(code="child1", parent_code="root"),
            _make_category(code="child2", parent_code="root"),
            _make_category(code="other", parent_code="other_parent"),
        ]
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories?parent_code=root")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert all(d["parent_code"] == "root" for d in data["data"])

    def test_filter_no_match(self, client, mock_repo):
        mock_repo.list_active.return_value = [
            _make_category(code="a", parent_code="x"),
        ]
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories?parent_code=nonexistent")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ── POST / (create) ─────────────────────────────────────────────────────────

class TestCreateCategory:
    def test_success(self, client, mock_repo):
        mock_repo.get_by_code.return_value = None
        mock_repo.create.return_value = _make_category(
            code="new", name="New", path="/new/", level=0,
        )
        _attach(client._app, mock_repo)

        resp = client.post("/api/skill-categories", json={
                "code": "new", "name": "New",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["code"] == "new"
        mock_repo.create.assert_called_once()

    def test_duplicate_code_returns_400(self, client, mock_repo):
        mock_repo.get_by_path.return_value = _make_category(code="dup", path="/dup/")
        mock_repo.get_by_code.return_value = None
        _attach(client._app, mock_repo)

        resp = client.post("/api/skill-categories", json={
                "code": "dup", "name": "Dup",
            })
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_invalid_parent_returns_400(self, client, mock_repo):
        # get_by_code("child") -> None (no duplicate), get_by_code("bad_parent") -> None (parent missing)
        mock_repo.get_by_code.return_value = None
        _attach(client._app, mock_repo)

        resp = client.post("/api/skill-categories", json={
                "code": "child", "name": "Child", "parent_code": "bad_parent",
            })
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    def test_create_root_category(self, client, mock_repo):
        mock_repo.get_by_code.return_value = None
        mock_repo.create.return_value = _make_category(
            code="root", name="Root", parent_code="", path="/root/", level=0,
        )
        _attach(client._app, mock_repo)

        resp = client.post("/api/skill-categories", json={
                "code": "root", "name": "Root", "parent_code": "",
            })
        assert resp.status_code == 200
        mock_repo.create.assert_called_once_with(
            code="root", name="Root", parent_code="", path="/root/", level=0, sort_order=0,
        )


# ── PUT /{code} (update) ───────────────────────────────────────────────────

class TestUpdateCategory:
    def test_success(self, client, mock_repo):
        cat = _make_category(code="cat", name="Updated", path="/cat/")
        mock_repo.get_by_path.return_value = cat
        mock_repo.update_by_path.return_value = cat
        _attach(client._app, mock_repo)

        resp = client.put("/api/skill-categories/cat", json={"name": "Updated"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_repo.update_by_path.assert_called_once_with("/cat/", name="Updated")

    def test_not_found_returns_404(self, client, mock_repo):
        mock_repo.get_by_path.return_value = None
        mock_repo.get_by_code.return_value = None
        _attach(client._app, mock_repo)

        resp = client.put("/api/skill-categories/missing", json={"name": "X"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_update_multiple_fields(self, client, mock_repo):
        cat = _make_category(code="cat", name="New", sort_order=10, status=0, path="/cat/")
        mock_repo.get_by_path.return_value = cat
        mock_repo.update_by_path.return_value = cat
        _attach(client._app, mock_repo)

        resp = client.put("/api/skill-categories/cat", json={
                "name": "New", "sort_order": 10, "status": 0,
            })
        assert resp.status_code == 200
        mock_repo.update_by_path.assert_called_once_with("/cat/", name="New", sort_order=10, status=0)

    def test_update_no_fields_sends_empty(self, client, mock_repo):
        cat = _make_category(code="cat", path="/cat/")
        mock_repo.get_by_path.return_value = cat
        mock_repo.update_by_path.return_value = cat
        _attach(client._app, mock_repo)

        resp = client.put("/api/skill-categories/cat", json={})
        assert resp.status_code == 200
        mock_repo.update_by_path.assert_called_once_with("/cat/")


# ── DELETE /{code} ──────────────────────────────────────────────────────────

class TestDeleteCategory:
    def test_success(self, client, mock_repo):
        cat = _make_category(code="cat", path="/cat/")
        mock_repo.get_by_path.return_value = cat
        mock_repo.list_active.return_value = [
            _make_category(code="cat", parent_code="", path="/cat/"),
        ]
        mock_repo.update_by_path.return_value = _make_category(code="cat", status=0, path="/cat/")
        _attach(client._app, mock_repo)

        resp = client.delete("/api/skill-categories/cat")
        assert resp.status_code == 200
        mock_repo.update_by_path.assert_called_once_with("/cat/", status=0)

    def test_not_found_returns_404(self, client, mock_repo):
        mock_repo.get_by_path.return_value = None
        mock_repo.get_by_code.return_value = None
        _attach(client._app, mock_repo)

        resp = client.delete("/api/skill-categories/missing")
        assert resp.status_code == 404

    def test_has_children_returns_400(self, client, mock_repo):
        parent = _make_category(code="parent", path="/parent/")
        mock_repo.get_by_path.return_value = parent
        mock_repo.list_active.return_value = [
            _make_category(code="parent", parent_code="", path="/parent/"),
            _make_category(code="child", parent_code="parent", path="/parent/child/"),
        ]
        _attach(client._app, mock_repo)

        resp = client.delete("/api/skill-categories/parent")
        assert resp.status_code == 400
        assert "active children" in resp.json()["detail"]


# ── GET /{code}/skills ──────────────────────────────────────────────────────

class TestListSkillsByCategory:
    def test_success(self, client, mock_repo):
        mock_repo.get_by_path.return_value = _make_category(code="cat", path="/cat/")
        mock_repo.get_by_code.return_value = None
        mock_repo.list_descendant_codes.return_value = ["cat"]
        mock_skill_repo = MagicMock()
        mock_skill_repo.list_skills.return_value = [
            {"id": "1", "name": "skill1", "category": "cat"},
            {"id": "2", "name": "skill2", "category": "other"},
        ]
        _attach(client._app, mock_repo, mock_skill_repo)

        resp = client.get("/api/skill-categories/cat/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 1
        assert data["data"][0]["name"] == "skill1"

    def test_category_not_found(self, client, mock_repo):
        mock_repo.get_by_path.return_value = None
        mock_repo.get_by_code.return_value = None
        _attach(client._app, mock_repo)

        resp = client.get("/api/skill-categories/missing/skills")
        assert resp.status_code == 404

    def test_pagination(self, client, mock_repo):
        mock_repo.get_by_path.return_value = _make_category(code="cat", path="/cat/")
        mock_repo.get_by_code.return_value = None
        mock_repo.list_descendant_codes.return_value = ["cat"]
        skills = [
            {"id": str(i), "name": f"skill{i}", "category": "cat"}
            for i in range(5)
        ]
        mock_skill_repo = MagicMock()
        mock_skill_repo.list_skills.return_value = skills
        _attach(client._app, mock_repo, mock_skill_repo)

        resp = client.get("/api/skill-categories/cat/skills?page=1&page_size=2")
        data = resp.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["data"]) == 2

    def test_descendant_categories_included(self, client, mock_repo):
        mock_repo.get_by_path.return_value = _make_category(code="root", path="/root/")
        mock_repo.get_by_code.return_value = None
        mock_repo.list_descendant_codes.return_value = ["root", "child"]
        mock_skill_repo = MagicMock()
        mock_skill_repo.list_skills.return_value = [
            {"id": "1", "name": "s1", "category": "root"},
            {"id": "2", "name": "s2", "category": "child"},
            {"id": "3", "name": "s3", "category": "unrelated"},
        ]
        _attach(client._app, mock_repo, mock_skill_repo)

        resp = client.get("/api/skill-categories/root/skills")
        data = resp.json()
        assert data["total"] == 2
        assert all(s["category"] in ("root", "child") for s in data["data"])
