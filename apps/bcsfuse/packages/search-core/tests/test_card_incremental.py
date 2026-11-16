"""Card change discovery stays bounded and retains recovery behind the cursor."""

import pytest
from search_core.providers.sql_catalog import sqlite_catalog_repository
from test_catalog_startup import Model, worker


def test_change_pages_include_equal_timestamps_and_tombstones(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    for key in ("a", "b", "c"):
        repo.upsert(key, {"name": key}, None, {})
    repo.remove("b")
    with repo.connect() as db:
        db.execute("UPDATE bcsfuse_agent_cards SET gmt_modify=100")
    pages = list(repo.iter_changes(100, 2))
    assert [key for rows, _ in pages for key, _ in rows] == ["a", "b", "c"]
    assert all(len(rows) <= 2 for rows, _ in pages)
    assert pages[-1][1] >= 100


def test_ten_thousand_unchanged_cards_need_one_range_query(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    with repo.connect() as db:
        db.executemany(
            "INSERT INTO bcsfuse_agent_cards "
            "(environment,agent_id,card_json,revision,gmt_modify) VALUES ('local',?,'{}',1,100)",
            [(str(i),) for i in range(10000)],
        )
    pages = list(repo.iter_changes(0, 1000))
    checkpoint = pages[-1][1]
    queries = []
    connect = repo.connect

    def traced():
        db = connect()
        db.set_trace_callback(queries.append)
        return db

    repo.connect = traced
    assert not any(rows for rows, _ in repo.iter_changes(checkpoint, 1000))
    selects = [q for q in queries if q.startswith("SELECT")]
    assert len(selects) == 1
    with connect() as db:
        plan = db.execute("EXPLAIN QUERY PLAN " + selects[0]).fetchall()
    assert any("USING INDEX" in row[3] and "gmt_modify" in row[3] for row in plan)


def test_idle_poll_does_not_scan_card_versions(tmp_path, monkeypatch):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    updates = worker(repo, Model())
    updates.run_once()

    def forbidden(*args):
        pytest.fail("idle round scanned every Card")

    monkeypatch.setattr(repo, "page", forbidden)
    updates.run_once()
    assert updates.embedding.calls == 1


def test_failed_card_retries_after_cursor_passes_it(tmp_path, monkeypatch):
    import search_core.catalog.updates as module

    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    model = Model()
    model.fail = True
    updates = worker(repo, model)
    updates.run_once()
    model.fail = False
    # No newly discovered versions: retry must be independent of the cursor.
    monkeypatch.setattr(repo, "iter_changes", lambda *args: iter(()))
    now[0] += 61
    updates.run_once()
    assert updates.status("a")["prepared"]
    assert model.calls == 2


def test_low_frequency_check_repairs_card_behind_cursor(tmp_path, monkeypatch):
    import search_core.catalog.updates as module

    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "time", lambda: now[0])
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    updates = worker(repo, Model())
    updates.run_once()
    repo.upsert("a", {"name": "changed invoice"}, None, {})
    with repo.connect() as db:
        db.execute("UPDATE bcsfuse_agent_cards SET gmt_modify=1")
    updates.run_once()
    assert updates.status("a")["local_applied_revision"] == 1
    now[0] += 601
    updates.run_once()
    assert updates.status("a")["local_applied_revision"] == 2


def test_new_revision_bypasses_old_failure_backoff(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    model = Model()
    model.fail = True
    updates = worker(repo, model)
    updates.run_once()
    model.fail = False
    repo.upsert("a", {"name": "updated invoice"}, None, {})
    updates.run_once()
    assert updates.status("a")["local_applied_revision"] == 2


def test_publication_stays_retryable_until_local_application(tmp_path, monkeypatch):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    updates = worker(repo, Model())
    original_sync = updates._sync
    # Emulate a published Doc not visible to timestamp replay this round.
    monkeypatch.setattr(updates, "_sync", lambda: None)
    updates.run_once()
    assert not updates.index.points
    monkeypatch.setattr(updates, "_sync", original_sync)
    monkeypatch.setattr(
        updates.documents, "iter_change_versions_since", lambda *args: ()
    )
    updates.run_once()
    assert "a" in updates.index.points
    assert updates.embedding.calls == 1


def test_range_bound_is_fixed_while_new_writes_arrive(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    for key in ("a", "b"):
        repo.upsert(key, {"name": key}, None, {})
    with repo.connect() as db:
        db.execute("UPDATE bcsfuse_agent_cards SET gmt_modify=100")
    pages = repo.iter_changes(0, 1)
    first, upper = next(pages)
    repo.upsert("c", {"name": "new arrival"}, None, {})
    with repo.connect() as db:
        db.execute(
            "UPDATE bcsfuse_agent_cards SET gmt_modify=? WHERE agent_id='c'",
            (upper + 1,),
        )
    assert [
        key for rows in [first, *(rows for rows, _ in pages)] for key, _ in rows
    ] == ["a", "b"]


def test_reconciliation_offsets_span_period_and_skip_missed_slots(
    tmp_path, monkeypatch
):
    import search_core.catalog.updates as module

    now = [1200.0]
    monkeypatch.setattr(module, "time", lambda: now[0])
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    from search_core.catalog.updates import CardUpdates
    from test_catalog_startup import Index

    workers = [
        CardUpdates(
            repo, Model(), Index(), embedding_key="model/2", instance_id=f"replica-{i}"
        )
        for i in range(24)
    ]
    for current in workers:
        current.run_once()
    deadlines = [current._reconcile_deadline for current in workers]
    assert all(1200 < deadline < 1800 for deadline in deadlines)
    assert max(deadlines) - min(deadlines) > 400
    now[0] += 1800
    current = workers[0]
    calls = []
    original = repo.page

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(repo, "page", counted)
    current.run_once()
    current.run_once()
    assert len(calls) == 1
    assert now[0] < current._reconcile_deadline <= now[0] + 600


def test_failed_reconcile_does_not_block_central_preparation(tmp_path, monkeypatch):
    import search_core.catalog.updates as module

    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "time", lambda: now[0])
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    updates = worker(repo, Model())
    updates.run_once()
    with repo.connect() as db:
        db.execute("UPDATE search_index_documents SET version=version+1,gmt_modify=1")
    repo.upsert("b", {"name": "new helper"}, None, {})
    updates.index.fail = True
    now[0] += 601
    updates.run_once()
    assert updates.documents.load_by_id("b") is not None
    assert not updates.ready
