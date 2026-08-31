"""Endpoint tests for ``/openapi/v1/bots/{bot_id}/config-manifest`` (W1, #1469).

A minimal FastAPI app hosts the bots router with the caller principal
overridden and the manifest/bot services bound to fakes via the injector —
the same harness as ``test_bots_endpoints.py``. The manifest service is the
REAL one over an in-memory SQLite repository: the all-or-nothing PUT and the
byte-exact script round-trip are claims mocks confirm vacuously.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.bots.router import router
from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (  # noqa: F401
    script_supported_for_bot,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_config_manifest_service import (
    ManifestServiceProtocol,
)
from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.services.manifest_service import (
    ManifestService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
)
# Side effect: registers the model on Base.metadata for create_all.
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)

DigEST = "sha256:" + "ef" * 32

BOT = {
    "id": 77,
    "bot_id": "b1",
    "bot_name": "N",
    "active_engine": "openclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
    "entity_id": "u1",
}


class InMemorySqliteDB:
    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture(autouse=True)
def _enable_the_dark_launch(monkeypatch):
    monkeypatch.setenv("BCM_API_ENABLED", "1")
    yield


@pytest.fixture
def svc():
    m = MagicMock()
    m.get_bot.return_value = BOT
    return m


@pytest.fixture
def startup_script():
    m = MagicMock()
    m.resolve_support.return_value = ("supported", "")
    return m


@pytest.fixture
def manifest_service():
    # StaticPool, not the sqlite default: TestClient serves the request on a
    # different thread, and an unpooled ``:memory:`` engine hands every
    # connection a fresh empty database — the create_all'd schema would live
    # on one thread's connection while the request queried another's. One
    # shared connection is exactly what the local plugin does for the same
    # reason. ``check_same_thread=False`` alone is what makes sharing legal.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # import_all_models (not the bare side-effect import): the manifest
    # model's registration on Base.metadata is guaranteed by the authoritative
    # eager importer regardless of this module's heavy import order.
    from agentclaw.community.core.schema import import_all_models

    import_all_models()
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return ManifestService(BotConfigManifestRepository(InMemorySqliteDB(engine)))


@pytest.fixture
def client(svc, startup_script, manifest_service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(ManifestServiceProtocol, to=manifest_service)
            binder.bind(BotStartupScriptServiceProtocol, to=startup_script)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


A_GOOD_DOC = {
    "schema_version": 1,
    "sources": {
        "content": {
            "git": "https://git.example/team/content.git",
            "ref": "v1.2.0",
            "auth": "corp-git",
        }
    },
    "manifest": {
        "identity": [
            {"type": "SOUL.md", "from": "content", "subpath": "bots/${OCB_BOT_ID}/soul.md"}
        ],
        "skills": [
            {"name": "q", "from": "content", "subpath": "skills/q/"},
            {"name": "zip", "source": "https://a.example/z.zip", "digest": DigEST},
        ],
    },
    "script": {"body": "#!/bin/bash\necho '$(id)' {token}\n"},
}


# --- dark launch -------------------------------------------------------------


def test_the_surface_answers_404_while_disabled(client, monkeypatch):
    monkeypatch.setenv("BCM_API_ENABLED", "")
    resp = client.get("/openapi/v1/bots/b1/config-manifest")
    assert resp.status_code == 404


# --- read side --------------------------------------------------------------


def test_get_returns_the_empty_document_when_absent(client):
    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest"))
    assert data["bot_id"] == "b1"
    assert data["schema_version"] == 1
    assert data["sources"] == {}
    assert data["script"] is None
    assert data["updated_by"] is None


# --- write side -------------------------------------------------------------


def test_put_stores_then_get_round_trips_byte_exact(client):
    import json

    result = _ok(client.put("/openapi/v1/bots/b1/config-manifest", json=A_GOOD_DOC))
    assert result["schema_version"] == 1
    assert result["warnings"] == []
    assert result["updated_by"] == "u1"

    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest"))
    assert data["manifest"]["skills"][0]["name"] == "q"
    # #1469:校正过的 script 正文逐字节往返(引号、$(id)、{token})。
    assert data["script"]["body"] == "#!/bin/bash\necho '$(id)' {token}\n"
    assert json.dumps(A_GOOD_DOC["sources"]["content"])  # sources 形状无损
    assert data["sources"]["content"]["ref"] == "v1.2.0"


def test_put_invalid_answers_422_with_per_entry_violations(client):
    body = {
        "sources": {"x": {}},
        "manifest": {"skills": [{"name": "q", "from": "ghost"}]},
    }
    resp = client.put("/openapi/v1/bots/b1/config-manifest", json=body)
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == 422000
    rules = {v["rule"] for v in payload["data"]["violations"]}
    assert "from-undeclared" in rules
    assert "skills-digest-required" in rules
    assert "sources-no-kind" in rules
    # 每条 violation 指名条目(#1469 验收字面)。
    assert all(v.get("entry") for v in payload["data"]["violations"])
    # 未写入:GET 仍读空文档。
    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest"))
    assert data["sources"] == {}


def test_put_all_or_nothing_an_invalid_replace_keeps_the_previous_document(client):
    _ok(client.put("/openapi/v1/bots/b1/config-manifest", json=A_GOOD_DOC))
    resp = client.put(
        "/openapi/v1/bots/b1/config-manifest",
        json={"manifest": {"skills": [{"name": "ghost", "from": "nobody"}]}},
    )
    assert resp.status_code == 422
    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest"))
    assert data["manifest"]["skills"][0]["name"] == "q"


def test_put_script_refused_when_the_935_judgment_says_no(client, startup_script):
    startup_script.resolve_support.return_value = ("unsupported", "legacy arca-direct")
    body = {"script": {"body": "#!/bin/bash\n"}}
    resp = client.put("/openapi/v1/bots/b1/config-manifest", json=body)
    assert resp.status_code == 422
    violations = resp.json()["data"]["violations"]
    assert any(v["rule"] == "script-unsupported" for v in violations)


def test_put_teclaw_refuses_script_at_the_engine_level(client, svc):
    svc.get_bot.return_value = {**BOT, "active_engine": "teclaw"}
    resp = client.put(
        "/openapi/v1/bots/b1/config-manifest", json={"script": {"body": "x"}}
    )
    assert resp.status_code == 422
    assert any(
        v["rule"] == "script-unsupported"
        for v in resp.json()["data"]["violations"]
    )


def test_unused_source_comes_back_as_a_warning(client):
    doc = dict(A_GOOD_DOC)
    doc["sources"] = {
        **doc["sources"],
        "spare": {"git": "https://g/spare.git", "ref": "v9"},
    }
    result = _ok(client.put("/openapi/v1/bots/b1/config-manifest", json=doc))
    assert any("sources.spare" in w for w in result["warnings"])


# --- delete -----------------------------------------------------------------


def test_delete_is_idempotent_and_reads_empty_afterwards(client):
    """组内 DELETE 契约与 startup-script 一致:幂等成功,恒答 deleted:true
    (服务层的 False 只是"本来就没有"的事,不进响应);声明已清由 GET 验证。"""
    _ok(client.put("/openapi/v1/bots/b1/config-manifest", json=A_GOOD_DOC))
    data = _ok(client.delete("/openapi/v1/bots/b1/config-manifest"))
    assert data == {"deleted": True}
    # idempotent success:重复 DELETE 不报错、不复活任何东西。
    data2 = _ok(client.delete("/openapi/v1/bots/b1/config-manifest"))
    assert data2 == {"deleted": True}
    after = _ok(client.get("/openapi/v1/bots/b1/config-manifest"))
    assert after["sources"] == {}
    assert after["manifest"]["skills"] == []


# --- capabilities_bridge ----------------------------------------------------


def test_capabilities_advertises_what_put_will_accept(client):
    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest/capabilities"))
    assert data["categories"]["skills"] is True
    assert data["categories"]["script"] is True
    # 读写一致性(capabilities 说不支持,PUT 必拒)。
    assert data["categories"]["engine_config"] is False
    assert "T3" in data["reasons"]["engine_config"]
    resp = client.put(
        "/openapi/v1/bots/b1/config-manifest",
        json={"manifest": {"engine_config": {"config": {"model": "x"}}}},
    )
    assert resp.status_code == 422


def test_capabilities_reflects_the_935_narrowing(client, startup_script):
    startup_script.resolve_support.return_value = ("unsupported", "local")
    data = _ok(client.get("/openapi/v1/bots/b1/config-manifest/capabilities"))
    assert data["categories"]["script"] is False


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    assert "request_id" in body
    return body["data"]
