"""Restoration controls readiness; pending Card preparation remains retryable."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from search_core.catalog.updates import CardUpdates
from search_core.providers.sql_catalog import sqlite_catalog_repository


class Model:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def embed(self, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return [1.0, 0.0]


class Index:
    def __init__(self):
        self.points = {}
        self.fail = False

    def upsert(self, points):
        if self.fail:
            raise RuntimeError("index unavailable")
        self.points.update({p.id: p for p in points})

    def delete(self, ids):
        for key in ids:
            self.points.pop(key, None)


def worker(repo, model, index=None):
    return CardUpdates(
        repo,
        model,
        index or Index(),
        embedding_key="model/2",
        instance_id="test",
        index_key="default",
    )


def test_ready_while_missing_card_embedding_is_still_blocked(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    entered, release = Event(), Event()

    class Slow(Model):
        def embed(self, text):
            entered.set()
            assert release.wait(5)
            return super().embed(text)

    model = Slow()
    updates = worker(repo, model)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(updates.run_once)
        try:
            assert entered.wait(3)
            assert updates.ready
            assert not updates.index.points
        finally:
            release.set()
        future.result()
    assert "a" in updates.index.points
    updates.run_once()
    assert model.calls == 1


def test_failed_preparation_does_not_block_readiness_and_retries_after_restart(
    tmp_path,
):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    model = Model()
    model.fail = True
    updates = worker(repo, model)
    updates.run_once()
    assert updates.ready
    assert updates.status("a")["preparation_error"] == "PREPARATION_FAILED"
    restarted_model = Model()
    restarted = worker(repo, restarted_model)
    restarted.run_once()
    assert restarted.ready and "a" in restarted.index.points
    assert restarted_model.calls == 1
    assert restarted.status("a")["preparation_error"] == ""


def test_restore_failure_blocks_readiness_and_retries_without_embedding(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    worker(repo, Model()).run_once()
    index, model = Index(), Model()
    index.fail = True
    restarted = worker(repo, model, index)
    restarted.run_once()
    assert not restarted.ready
    assert model.calls == 0
    index.fail = False
    restarted.run_once()
    assert restarted.ready and "a" in index.points
    assert model.calls == 0


def test_ready_endpoint_succeeds_before_background_embedding_finishes(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from search_core.bootstrap.local import create_local_app
    from search_core.providers.demo_embedding import DemoEmbedding

    repo = sqlite_catalog_repository(str(tmp_path / "cards.sqlite3"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    entered, release = Event(), Event()
    original = DemoEmbedding.embed

    def slow(self, text):
        entered.set()
        assert release.wait(5)
        return original(self, text)

    monkeypatch.setattr(DemoEmbedding, "embed", slow)
    with TestClient(
        create_local_app(tmp_path, poll_seconds=0.05),
        client=("127.0.0.1", 50000),
        base_url="http://127.0.0.1",
    ) as client:
        try:
            assert entered.wait(3)
            response = client.get("/ready?include_counts=true")
            assert response.status_code == 200, response.text
            assert response.json()["index_counts"]["vector_points"] == 0
        finally:
            release.set()


def test_restart_does_not_expose_deleted_card_while_other_card_is_preparing(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("b", {"name": "deleted invoice helper"}, None, {})
    worker(repo, Model()).run_once()
    repo.remove("b")
    repo.upsert("a", {"name": "new helper"}, None, {})

    class CheckDuringPreparation(Model):
        def embed(self, text):
            assert restarted.ready
            assert "b" not in restarted.searchable_ids()
            return super().embed(text)

    restarted = worker(repo, CheckDuringPreparation())
    restarted.run_once()
    assert restarted.status("a")["prepared"]
    assert restarted.status("b")["prepared"]
    assert "b" not in restarted.searchable_ids()
