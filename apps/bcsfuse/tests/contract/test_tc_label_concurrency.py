"""Legacy visibility edits preserve labels committed after their initial read."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from app.tc.catalog import TcCatalog
from app.tc.v2 import CardOperations
from search_core.providers.sql_catalog import sqlite_catalog_repository


@pytest.mark.parametrize("operation", ["update", "sync", "v2_availability"])
def test_visibility_edit_preserves_concurrent_labels(tmp_path, monkeypatch, operation):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert(
        "one",
        {"name": "Worker", "availability": "public", "runtime_state": "online"},
        "agent",
        {},
        labels={"visibility": ["public"], "user_visibility": ["public"]},
    )
    catalog = TcCatalog(repo)

    def write_after_competing_labels(*args, **kwargs):
        repo.upsert(
            "one",
            {},
            None,
            {},
            labels={
                "visibility": ["public"],
                "user_visibility": ["protected"],
                "topic": ["concurrent"],
            },
        )
        return repo.upsert(*args, **kwargs)

    catalog.write_card = write_after_competing_labels
    runtime = SimpleNamespace(
        updates=SimpleNamespace(upsert=write_after_competing_labels),
        caches=SimpleNamespace(changing=lambda *_: nullcontext()),
    )
    monkeypatch.setattr("app.tc.v2.services", lambda _: (catalog, runtime))
    if operation == "update":
        catalog.update("one", {"availability": "private"})
    elif operation == "sync":
        catalog.sync("one", {"name": "Worker", "availability": "private"})
    else:
        CardOperations(None).upsert("one", {"availability": "private"}, None, {})
    assert repo.get("one").card.labels == {
        "visibility": ["private"],
        "user_visibility": ["protected"],
        "topic": ["concurrent"],
    }
