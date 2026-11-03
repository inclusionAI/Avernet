"""Shared build orchestration works with ordinary documents, without Card/TC code."""

import sqlite3
import time
from contextlib import contextmanager

import pytest


class Source:
    def __init__(self):
        self.revisions = {"document-1": 1, "document-2": 1}
        self.prepared = {}
        self.calls = []
        self.fail = ""

    def page(self, after, limit):
        return sorted((key, rev) for key, rev in self.revisions.items() if key > after)[
            :limit
        ]

    def prepared_revision(self, index_key, source_id):
        return self.prepared.get((index_key, source_id))

    def rebuild(self, source_id, config, model, lease_seconds):
        self.calls.append(source_id)
        if source_id == self.fail:
            raise OSError("synthetic processing failure")
        assert len(model.embed(source_id)) == config.embedding_dimension
        self.prepared[config.index_key, source_id] = self.revisions[source_id]


@contextmanager
def factory(name, dimension):
    class Model:
        def embed(self, text):
            return [1.0] * dimension

    model = Model()
    model.dimension = dimension
    yield model


def world(tmp_path, environment="test"):
    from search_core.core.index_builds import IndexBuildService
    from search_core.providers.sql_index_builds import (
        SQLITE_SCHEMA,
        SqlIndexBuildRepository,
    )

    path = tmp_path / "builds.db"

    def connect():
        return sqlite3.connect(path)

    with connect() as db:
        db.execute(SQLITE_SCHEMA)
    repo = SqlIndexBuildRepository(connect, environment=environment, placeholder="?")
    source = Source()
    service = IndexBuildService(
        source, repo, factory, conversion_version="text-v1", filter_fields=frozenset()
    )
    return source, repo, service


def test_new_generation_keeps_existing_documents_and_immutable_config(tmp_path):
    from search_core.api.index_builds import IndexBuildConflict

    source, repo, service = world(tmp_path)
    first = service.create("old", "model-a", 64).config
    service.run_once()
    before = source.prepared.copy()
    second = service.create("new", "model-b", 128).config
    with pytest.raises(ValueError):
        service.select("new")
    with pytest.raises(IndexBuildConflict):
        service.create("old", "different-model", 64)
    service.run_once()
    assert first.index_key != second.index_key
    assert all(source.prepared[key] == value for key, value in before.items())
    assert service.select("new") == second
    assert repo.get("new").completed_count == 2


def test_other_runner_resumes_failed_work_without_repeating_finished_sources(tmp_path):
    from search_core.core.index_builds import IndexBuildService

    source, repo, service = world(tmp_path)
    service.create("retry", "model", 64)
    source.fail = "document-2"
    service.run_once()
    assert repo.get("retry").status == "failed"
    source.fail = ""
    other = IndexBuildService(
        source, repo, factory, conversion_version="text-v1", filter_fields=frozenset()
    )
    other.create("retry", "model", 64)
    other.run_once()
    assert repo.get("retry").status == "completed"
    assert source.calls.count("document-1") == 1
    assert source.calls.count("document-2") == 2


def test_sql_execution_ownership_and_environment_are_preserved(tmp_path):
    _, repo, service = world(tmp_path, "pre")
    _, other_repo, other = world(tmp_path, "prod")
    service.create("job", "one", 64)
    other.create("job", "two", 128)
    assert repo.claim("job", "owner", 0.05)
    assert not repo.claim("job", "other", 60)
    time.sleep(0.06)
    assert repo.claim("job", "other", 60)
    assert not repo.finish("job", "owner", "completed", 2, 2, "")
    assert other_repo.get("job").status == "pending"


def test_build_config_roundtrips_with_shared_identity():
    from search_core.config.index_builds import IndexBuildConfig

    cfg = IndexBuildConfig(
        build_id="same-version",
        embedding_model="model",
        embedding_dimension=64,
        conversion_version="agent-card-v3",
        filter_fields=("resource_type",),
    )
    assert cfg.model_dump(mode="json") == {
        "build_id": "same-version",
        "embedding_model": "model",
        "embedding_dimension": 64,
        "conversion_version": "agent-card-v3",
        "filter_fields": ["resource_type"],
    }
    assert cfg.index_key == "same-version"
    assert IndexBuildConfig.model_validate_json(cfg.model_dump_json()) == cfg


def test_build_freezes_requested_filters_and_retry_uses_saved_defaults(tmp_path):
    from search_core.api.index_builds import IndexBuildConflict

    _, _, service = world(tmp_path)
    cfg = service.create("labels", "model", 2, filter_fields={"category"}).config
    service.filter_fields = {"region"}
    assert service.create("labels", "model", 2).config == cfg
    assert cfg.filter_fields == ("category",)
    with pytest.raises(IndexBuildConflict):
        service.create("labels", "model", 2, filter_fields={"region"})
    with pytest.raises(ValueError):
        service.create("invalid", "model", 2, filter_fields={"index_key"})


def test_shared_management_http_preserves_auth_status_and_conflicts(tmp_path):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient
    from search_core.adapters.http.index_builds import create_index_build_router
    from search_core.api.models import VerifiedIdentity

    _, _, service = world(tmp_path)

    async def operator(request: Request):
        if request.headers.get("x-operator") != "test":
            raise HTTPException(403)
        return VerifiedIdentity("test", None)

    app = FastAPI()
    app.include_router(create_index_build_router(service, operator))
    path = "/api/v1/index-builds/ordinary-documents"
    body = {"embedding_model": "plain-model", "embedding_dimension": 64}
    headers = {"x-operator": "test"}
    with TestClient(app) as client:
        assert client.put(path, json=body).status_code == 403
        assert client.get(path).status_code == 403
        assert client.put(path, json=body, headers=headers).status_code == 202
        service.run_once()
        status = client.get(path, headers=headers)
        assert status.status_code == 200 and status.json()["status"] == "completed"
        assert (
            client.put(
                path, json={**body, "embedding_dimension": 128}, headers=headers
            ).status_code
            == 409
        )
