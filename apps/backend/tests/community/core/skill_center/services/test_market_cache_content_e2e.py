"""E2E: market cache produces non-empty content from local FS + DB.

This test nails down the *real* content source behind ``get_market_tree``
/ ``search_market_skills``: the market repo dir on the **filesystem**
(scanned by ``_build_market_tree_sync``) plus git skills in the **DB**
(via ``SkillRepository``). It does **not** touch ``SkillCenterClient`` —
the SkillService never references it. By exercising the full
``sync_skills_from_git -> _refresh_market_cache -> get_market_tree`` chain
against a temp FS market repo + in-memory repo, we prove a local box can
serve non-empty market content with no remote skill-center mock.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.services.skill_service import SkillService


pytestmark = pytest.mark.integration


class InMemorySkillRepo:
    """Stateful in-memory SkillRepository (FS+DB e2e — sync writes, refresh reads).

    Deliberately omits ``list_git_skills_with_order`` so the service takes
    the fallback branch (``_list_git_skills_sync`` reads via ``list_skills``),
    keeping the DB content source explicit.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._next_id = 1

    def list_skills(self, user_id=None, bolt_id="default", path=None):
        return list(self._rows.values())

    def create(self, skill_data: dict) -> dict:
        sid = str(self._next_id)
        self._next_id += 1
        # Real DB-backed rows are JSON-serializable (the cache json.dumps
        # them); normalize datetimes to ISO strings to mirror that.
        row = {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in skill_data.items()
        }
        row["id"] = sid
        self._rows[sid] = row
        return row

    def update(self, skill_id: str, skill_data: dict) -> dict | None:
        row = self._rows.get(str(skill_id))
        if row is None:
            return None
        row.update({
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in skill_data.items()
        })
        return row

    def delete(self, skill_id: str) -> bool:
        return self._rows.pop(str(skill_id), None) is not None


def _make_skill(market: Path, rel: str, name: str, description: str) -> None:
    d = market / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "category: business\n"
        "tags: [aml, demo]\n"
        "---\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def _make_memory_cache() -> MarketCache:
    """Real MarketCache pinned to memory-only (no ZCache probe)."""
    import time as _time

    cache = MarketCache(cache_plugin=MagicMock())
    cache._zcache_available = False
    cache._zcache_checked_at = _time.time() + 1e9
    return cache


def _lenient_sync():
    """Mirror LocalSkillRepoSyncPlugin: get_scan_target returns fallback."""
    stub = MagicMock()
    stub.get_scan_target.side_effect = lambda default: default
    return stub


@pytest.fixture
def market_svc(tmp_path):
    market = tmp_path / "market"
    market.mkdir()
    _make_skill(market, "business/aml/complaint", "complaint", "AML complaint helper")
    _make_skill(market, "business/aml/screening", "screening", "AML screening helper")

    # repo_dir IS the market repo: _get_market_repo_dir falls back to repo_dir
    # (global is forced non-existent by the autouse conftest fixture).
    svc = SkillService(
        skill_repo=InMemorySkillRepo(),
        skill_repo_sync=_lenient_sync(),
        category_repo=MagicMock(),
        market_cache=_make_memory_cache(),
        device_fs_factory=MagicMock(),
        git_sync_service_factory=MagicMock(),
        active_dir=tmp_path / "active",
        repo_dir=market,
        local_dir=tmp_path / "local",
    )
    return svc


def test_market_cache_builds_non_empty_content(market_svc):
    sync_result = market_svc.sync_skills_from_git()
    assert sync_result["created"] == 2
    assert sync_result["failed"] == 0

    # Tree built from FS market repo dir
    tree = market_svc.get_market_tree()
    assert tree, "market tree must be non-empty (FS source)"

    # Skills list built from DB
    git_skills = market_svc.list_git_skills()
    names = {s["name"] for s in git_skills}
    assert names == {"complaint", "screening"}

    # Search hits content seeded into the DB
    hits = market_svc.search_market_skills("complaint")
    assert any(h["name"] == "complaint" for h in hits)
