"""Lost local indexes must not trust an unchanged in-memory Doc checkpoint."""

import pytest
from search_core.providers.sql_catalog import sqlite_catalog_repository
from test_catalog_startup import Index, Model, worker


class CountedIndex(Index):
    def __init__(self):
        super().__init__()
        self.keyword = {}
        self.writes = 0

    def upsert(self, points):
        super().upsert(points)
        self.keyword.update({p.id: p for p in points})
        self.writes += 1

    def delete(self, ids):
        super().delete(ids)
        for key in ids:
            self.keyword.pop(key, None)

    def get_vector_ids(self):
        return sorted(self.points.keys() | self.keyword.keys())

    def point_counts(self, index_key=""):
        result = {}
        for name, points in (("vector", self.points), ("keyword", self.keyword)):
            matching = sum(
                p.payload.get("index_key") == index_key for p in points.values()
            )
            result.update(
                {
                    f"{name}_points": len(points),
                    f"{name}_matching_points": matching,
                    f"{name}_mismatched_points": len(points) - matching,
                }
            )
        return result


def setup_catalog(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("live", {"name": "invoice"}, None, {})
    repo.upsert("deleted", {"name": "removed"}, None, {})
    updates = worker(repo, Model(), CountedIndex())
    updates.run_once()
    repo.remove("deleted")
    updates.run_once()
    return updates


@pytest.mark.parametrize("branch", ["vector", "keyword", "both"])
def test_reconcile_repairs_point_loss_without_embedding(tmp_path, branch):
    updates = setup_catalog(tmp_path)
    model_calls = updates.embedding.calls
    versions = dict(updates._document_versions)
    if branch in ("vector", "both"):
        updates.index.points.clear()
    if branch in ("keyword", "both"):
        updates.index.keyword.clear()
    assert not updates.index_counts_ready(updates.index.point_counts("default"))
    updates._reconcile_deadline = 0
    updates.run_once()
    assert updates.ready
    assert updates.index_counts_ready(updates.index.point_counts("default"))
    assert set(updates.index.points) == set(updates.index.keyword) == {"live"}
    assert updates._document_versions == versions
    assert updates.embedding.calls == model_calls


def test_failed_repair_stays_retryable_and_not_ready(tmp_path):
    updates = setup_catalog(tmp_path)
    updates.index.points.clear()
    updates.index.fail = True
    updates._reconcile_deadline = 0
    updates.run_once()
    assert not updates.ready
    assert not updates.index_counts_ready(updates.index.point_counts("default"))
    updates.index.fail = False
    updates.run_once()
    assert updates.ready
    assert set(updates.index.points) == {"live"}
    assert not updates._errors


def test_healthy_reconcile_does_not_rewrite_vectors(tmp_path):
    updates = setup_catalog(tmp_path)
    writes = updates.index.writes
    updates._reconcile_deadline = 0
    updates.run_once()
    assert updates.index.writes == writes


def test_reconcile_removes_stray_points_and_replays_tombstones(tmp_path):
    updates = setup_catalog(tmp_path)
    point = updates.index.points["live"]
    updates.index.upsert(
        [
            point.model_copy(update={"id": "stray"}),
            point.model_copy(update={"id": "deleted"}),
        ]
    )
    updates._reconcile_deadline = 0
    updates.run_once()
    assert set(updates.index.points) == set(updates.index.keyword) == {"live"}
    assert updates.index_counts_ready(updates.index.point_counts("default"))


def test_unrepaired_coverage_does_not_become_ready_after_round(tmp_path, monkeypatch):
    updates = setup_catalog(tmp_path)
    updates.index.points.clear()
    monkeypatch.setattr(updates.index, "upsert", lambda points: None)
    updates._reconcile_deadline = 0
    with pytest.raises(RuntimeError, match="coverage repair incomplete"):
        updates.run_once()
    assert not updates.ready
