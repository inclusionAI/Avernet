"""Public Card management works with SQLite and no business application installed."""

import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from search_core.adapters.http.cards import create_card_router
from search_core.api.cards import CatalogConflict
from search_core.api.models import VerifiedIdentity
from search_core.core.card_management import CardManagementService
from search_core.providers.sql_cards import sqlite_card_repository

PATH = "/api/v1/agent-cards/example:one"


class Management(CardManagementService):
    def status(self, agent_id):
        row = self.read(agent_id)
        return {
            "agent_id": agent_id,
            "saved_revision": row.revision,
            "deleted": row.card.deleted,
            "prepared": False,
            "preparation_error": "",
            "instance_id": "test",
            "environment": self.repository.environment,
            "index_key": "test",
            "local_applied_revision": 0,
            "local_error": "",
            "operations_text_revision": 0,
        }


async def operator():
    return VerifiedIdentity("operator", None)


@pytest.fixture
def setup(tmp_path):
    repo = sqlite_card_repository(str(tmp_path / "cards.db"))
    app = FastAPI()
    app.include_router(create_card_router(Management(repo), operator))
    with TestClient(app) as client:
        yield repo, client


def test_roundtrip_merge_delete_and_recreate(setup):
    repo, client = setup
    card = {
        "custom": {"a": 1, "b": 2},
        "status": "inactive",
        "deleted": True,
        "version": {"business": 7},
        "values": [1, 2],
    }
    saved = client.put(PATH, json={"card": card, "resource_type": "group"})
    assert saved.status_code == 202
    assert client.get(PATH).json()["card"] == card
    with repo.connect() as conn:
        stored = json.loads(
            conn.execute("SELECT card_json FROM bcsfuse_agent_cards").fetchone()[0]
        )
    assert stored["source_card"] == card and stored["deleted"] is False
    updated = client.put(PATH, json={"card": {"custom": {"b": None}, "values": []}})
    assert updated.status_code == 202
    details = client.get(PATH).json()
    assert details["card"] == {**card, "custom": {"a": 1, "b": None}, "values": []}
    assert details["resource_type"] == "group"
    assert client.put(PATH, json={"card": {"values": []}}).json() == updated.json()
    assert client.delete(PATH).json() == {"deleted": True}
    assert client.delete(PATH).status_code == 202
    assert client.delete(PATH + "-missing").status_code == 202
    assert client.get(PATH).status_code == 404
    assert client.get(PATH + "/index-status").json()["deleted"] is True
    assert client.put(PATH, json={"card": {"replacement": True}}).status_code == 202
    assert client.get(PATH).json()["card"] == {"replacement": True}


def test_card_read_only_queries_source_and_does_not_require_artifacts(setup):
    repo, client = setup
    assert (
        client.put(PATH, json={"card": {"nested": {"unknown": 3}}}).status_code == 202
    )
    statements, connect = [], repo.connect

    def traced():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn

    repo.connect = traced
    assert client.get(PATH).status_code == 200
    assert len([s for s in statements if s.startswith("SELECT")]) == 1
    assert not any("search_index_documents" in s for s in statements)
    assert client.get(PATH + "/card").status_code == 404


def test_conflicts_and_failures_do_not_report_success(setup, monkeypatch):
    repo, client = setup
    assert client.put(PATH, json={"card": {"a": 1}}).status_code == 202
    assert (
        client.put(PATH, json={"card": {"a": 2}, "resource_type": "group"}).status_code
        == 409
    )
    with pytest.raises(CatalogConflict):
        repo.delete("example:one", 0)

    def fail(*args):
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr(repo, "connect", fail)
    assert client.put(PATH, json={"card": {"a": 3}}).status_code == 503
    assert client.delete(PATH).status_code == 503
    assert client.get(PATH).status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"card": {}},
        {"card": {"x": 1}, "expected_revision": 0},
        {"card": {"x": 1}, "source_version": "upstream"},
    ],
)
def test_write_contract_rejects_envelope_errors(setup, body):
    _, client = setup
    assert client.put(PATH, json=body).status_code == 422


def test_authentication_is_required_for_every_route(tmp_path):
    async def deny():
        raise HTTPException(403, "denied")

    app = FastAPI()
    app.include_router(
        create_card_router(
            Management(sqlite_card_repository(str(tmp_path / "c.db"))), deny
        )
    )
    with TestClient(app) as client:
        assert client.put(PATH, json={"card": {"x": 1}}).status_code == 403
        assert client.delete(PATH).status_code == 403
        assert client.get(PATH).status_code == 403
        assert client.get(PATH + "/index-status").status_code == 403


def test_public_card_storage_import_and_execution_need_no_application():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
import tempfile
from pathlib import Path

class NoApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        assert fullname.split('.')[0] not in {'app', 'bcsfuse_internal', 'zbcsfuse_internal'}, fullname

sys.meta_path.insert(0, NoApplication())
from search_core.adapters.http.cards import create_card_router
from search_core.providers.sql_cards import sqlite_card_repository
with tempfile.TemporaryDirectory() as path:
    repo = sqlite_card_repository(str(Path(path) / 'card.db'))
    repo.upsert('public', {'new_field': {'nested': [1, None]}}, None, {})
    assert repo.read_card('public').card.source_card == {'new_field': {'nested': [1, None]}}
    repo.remove('public')
    assert repo.read_card('public').card.deleted
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
