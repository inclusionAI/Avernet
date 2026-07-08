"""Skill Category management — CRUD + tree endpoint."""
from typing import Optional

from agentclaw.community.adapters.http.skill_center.schemas import (
    CreateCategoryRequest,
    UpdateCategoryRequest,
    CategoryResponse,
    CategoryTreeNode,
    CategoryTreeResponse,
    CategoryDetailResponse,
    CategoryListResponse,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillCategoryRepository,
    SkillRepository,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from fastapi import APIRouter, HTTPException


logger = get_logger()

router = APIRouter(prefix="/api/skill-categories", tags=["skill-categories"])


def _get_by_path_or_code(repo, code_or_path: str) -> Optional[dict]:
    """Resolve route param to category: try path first, fallback to code."""
    path = f"/{code_or_path}/" if not code_or_path.startswith("/") else code_or_path
    result = repo.get_by_path(path)
    if result:
        return result
    return repo.get_by_code(code_or_path)


def _build_path(parent_code: str, code: str, repo) -> tuple[str, int]:
    """Build materialized path and compute level from parent."""
    if not parent_code:
        return f"/{code}/", 0
    parent = repo.get_by_code(parent_code)
    if not parent:
        raise ValueError(f"Parent category '{parent_code}' not found")
    return f"{parent['path']}{code}/", parent['level'] + 1


def _build_tree(categories: list[dict]) -> list[dict]:
    """Build tree from flat list using path as key (handles non-unique codes)."""
    by_path = {c["path"]: {**c, "children": []} for c in categories}
    roots = []
    for node in by_path.values():
        p = node["path"]
        # Absolute root: path is exactly "/"
        if p == "/":
            roots.append(node)
            continue
        parts = p.rstrip("/").split("/")
        parent_path = "/".join(parts[:-1]) + "/"
        if parent_path in by_path:
            by_path[parent_path]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _to_tree_node(node: dict) -> CategoryTreeNode:
    """Recursively convert dict to CategoryTreeNode."""
    return CategoryTreeNode(
        code=node["code"],
        name=node["name"],
        parent_code=node.get("parent_code") or "",
        path=node.get("path"),
        level=node.get("level", 0),
        sort_order=node.get("sort_order", 0),
        status=node.get("status", 1),
        children=[_to_tree_node(c) for c in node.get("children", [])],
    )


@router.get("/tree", response_model=CategoryTreeResponse)
async def get_category_tree(
    repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
):
    """Get all categories as a tree structure."""
    flat = repo.list_active()
    tree = _build_tree(flat)
    tree_nodes = [_to_tree_node(n) for n in tree]
    return CategoryTreeResponse(success=True, data=tree_nodes)


@router.get("", response_model=CategoryListResponse)
async def list_categories(
    parent_code: str = None,
    repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
):
    """List categories, optionally filtered by parent_code."""
    categories = repo.list_active()
    if parent_code is not None:
        categories = [
            c
            for c in categories
            if c.get("parent_code") == parent_code
        ]
    return CategoryListResponse(
        success=True,
        data=[CategoryResponse(**c) for c in categories],
    )


@router.post("", response_model=CategoryDetailResponse)
async def create_category(
    request: CreateCategoryRequest,
    repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
):
    """Create a new category."""
    try:
        path, level = _build_path(request.parent_code, request.code, repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing = repo.get_by_path(path)
    if existing:
        raise HTTPException(status_code=400, detail=f"Category path '{path}' already exists")

    category = repo.create(
        code=request.code,
        name=request.name,
        parent_code=request.parent_code or "",
        path=path,
        level=level,
        sort_order=request.sort_order,
    )
    return CategoryDetailResponse(success=True, data=CategoryResponse(**category))


@router.put("/{code:path}", response_model=CategoryDetailResponse)
async def update_category(
    code: str,
    request: UpdateCategoryRequest,
    repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
):
    """Update a category by code or path."""
    category = _get_by_path_or_code(repo, code)
    if not category:
        raise HTTPException(status_code=404, detail=f"Category '{code}' not found")

    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.sort_order is not None:
        updates["sort_order"] = request.sort_order
    if request.status is not None:
        updates["status"] = request.status

    updated = repo.update_by_path(category["path"], **updates)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Category '{code}' not found")
    return CategoryDetailResponse(success=True, data=CategoryResponse(**updated))


@router.delete("/{code:path}", response_model=CategoryDetailResponse)
async def delete_category(
    code: str,
    repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
):
    """Delete a category (set status=0). Fails if it has active children."""
    category = _get_by_path_or_code(repo, code)
    if not category:
        raise HTTPException(status_code=404, detail=f"Category '{code}' not found")

    all_categories = repo.list_active()
    children_count = sum(
        1
        for c in all_categories
        if c.get("parent_code") == category["code"]
        and c.get("path", "").startswith(category["path"])
    )
    if children_count > 0:
        raise HTTPException(status_code=400, detail="Cannot delete category with active children")

    updated = repo.update_by_path(category["path"], status=0)
    return CategoryDetailResponse(success=True, data=CategoryResponse(**updated))


@router.get("/{code:path}/skills")
async def list_skills_by_category(
    code: str,
    page: int = 1,
    page_size: int = 20,
    category_repo: SkillCategoryRepository = Injected(SkillCategoryRepository),
    skill_repo: SkillRepository = Injected(SkillRepository),
):
    """List skills under a category and all its descendants."""
    category = _get_by_path_or_code(category_repo, code)
    if not category:
        raise HTTPException(status_code=404, detail=f"Category '{code}' not found")

    cat_path = category["path"]
    descendant_codes = category_repo.list_descendant_codes(cat_path)

    all_skills = skill_repo.list_skills()
    filtered = [
        s
        for s in all_skills
        if s.get("category") in descendant_codes
        or (s.get("category_path") or "").startswith(cat_path)
    ]

    total = len(filtered)
    start = (page - 1) * page_size
    skills = filtered[start:start + page_size]

    return {
        "success": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": skills,
    }
