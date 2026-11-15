"""Shared bounded snapshot/change application, including TC compatibility."""

from types import SimpleNamespace

import pytest
from search_core.core.index_sync import apply_change_pages, apply_snapshot_pages


def test_snapshot_applies_bounded_pages_and_tracks_stale_ids():
    applied = []
    stale = {"a", "stale"}
    count = apply_snapshot_pages(
        [[SimpleNamespace(id="a")], [SimpleNamespace(id="b")]],
        2,
        applied.extend,
        stale,
    )
    assert count == 2 and [p.id for p in applied] == ["a", "b"]
    assert stale == {"stale"}
    with pytest.raises(ValueError):
        apply_snapshot_pages([[]], 2, applied.extend, stale)


def test_checkpoint_advances_only_after_both_mutations_succeed():
    applied, deleted, checkpoints = [], [], []
    changes = [SimpleNamespace(upserts=["a"], deleted_ids=["b"], checkpoint=2)]
    assert apply_change_pages(
        changes, 1, applied.extend, deleted.extend, checkpoints.append
    ) == {"upserted": 1, "deleted": 1}
    assert applied == ["a"] and deleted == ["b"] and checkpoints == [2]

    def fail(_):
        raise OSError("index unavailable")

    with pytest.raises(OSError):
        apply_change_pages(changes, 1, applied.extend, fail, checkpoints.append)
    assert checkpoints == [2]
