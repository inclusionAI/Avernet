"""Endpoint tests for ``/openapi/v1/source-credentials`` (W3, #1471).

A minimal FastAPI app hosts the group's router with the caller principal
overridden and the real service graph over an in-memory SQLite repository
(the fail-closed and all-or-nothing refusals are storage claims — a mock
would confirm them vacuously). Prefix-boundary semantics live in the
policy suite; these pin the wire contract. REFUSED admission routes every
machine caller away, so the tests inject a user principal directly the
way the group's own seam allows.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.adapters.http.openapi_v1.source_credentials import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.api.source_credential_service import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialNotFoundError,
    MasterKeyUnavailableError,
)
from agentclaw.community.core.bot_config_manifest.credentials.service import (
    SourceCredentialService,
)
from agentclaw.community.core.bot_management.token_vault import (
    CIPHER_PREFIX,
    TokenVault,
)
from agentclaw.community.core.repository.implementations.bot.source_credential import (
    SourceCredentialRepository,
)
from tests.community.core.bot_config_manifest.credentials.repo_helper import (
    InMemorySqliteDB,
)

A_BODY = {
    "type": "header",
    "header_name": "PRIVATE-TOKEN",
    "secret": "Bearer hush-token",
    "allowed_prefixes": ["https://git.example/team/content"],
}


class InMemorySqlite:
    def __init__(self):
        # StaticPool: TestClient 的请求跑在另一线程,非池化内存库给每个
        # 连接发一个空库(表建在这条连接、查询在另一条——"no such table")。
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from agentclaw.community.core.schema import import_all_models
        from agentclaw.community.core.base import Base

        import_all_models()
        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine, autoflush=False)

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


@pytest.fixture
def service():
    return SourceCredentialService(
        SourceCredentialRepository(InMemorySqlite()), TokenVault("master-key-material")
    )


@pytest.fixture
def client(service):
    class _M(Module):
        def configure(self, binder):
            binder.bind(SourceCredentialServiceProtocol, to=service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "u1")


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    return body["data"]


# --- PUT/GET round trip -----------------------------------------------------


def test_put_answers_masked_and_get_reads_it_back(client, service):
    data = _ok(client.put("/openapi/v1/source-credentials/corp-git", json=A_BODY))
    assert data["name"] == "corp-git"
    assert data["has_secret"] is True
    assert "Bearer hush-token" not in str(data)
    assert "secret" not in data  # masked 形态的键集合里根本没有它

    got = _ok(client.get("/openapi/v1/source-credentials/corp-git"))
    assert got["header_name"] == "PRIVATE-TOKEN"
    assert got["allowed_prefixes"] == A_BODY["allowed_prefixes"]
    assert "hush-token" not in str(got)

    # 存储侧:密文,不是明文(公开面之外,服务真库断言)。
    row = service._repository.get(name="corp-git")
    assert row.secret_ciphertext.startswith(CIPHER_PREFIX)
    assert "hush-token" not in row.secret_ciphertext
    # 审计:principal 组合的 actor 已落列(此前该列永远空串——终审 F1)。
    assert row.modifier == "u1"


def test_gets_404_masked(client):
    resp = client.get("/openapi/v1/source-credentials/ghost")
    assert resp.status_code == 404
    assert resp.json()["data"] is None


def test_put_422_refuses_reserved_and_bad_prefixes(client):
    resp = client.put(
        "/openapi/v1/source-credentials/corp-git",
        json={**A_BODY, "type": "basic"},
    )
    assert resp.status_code == 422

    resp = client.put(
        "/openapi/v1/source-credentials/corp-git",
        json={**A_BODY, "allowed_prefixes": []},
    )
    assert resp.status_code == 422
    # 未写入。
    assert _ok(client.get("/openapi/v1/source-credentials")) == []


def test_put_422_for_invalid_header_name(client):
    resp = client.put(
        "/openapi/v1/source-credentials/corp-git",
        json={**A_BODY, "header_name": "Not A Header"},
    )
    assert resp.status_code == 422


def test_rotation_replaces_in_place(client, service):
    client.put("/openapi/v1/source-credentials/corp-git", json=A_BODY)
    _ok(
        client.put(
            "/openapi/v1/source-credentials/corp-git",
            json={**A_BODY, "secret": "Bearer rotated"},
        )
    )
    rows = service._repository.list()
    assert len(rows) == 1
    binding = service.binding(name="corp-git")
    assert (
        binding.headers_for(A_BODY["allowed_prefixes"][0])["PRIVATE-TOKEN"]
        == "Bearer rotated"
    )


def test_delete_is_idempotent_and_404_afterwards(client):
    client.put("/openapi/v1/source-credentials/corp-git", json=A_BODY)
    assert _ok(client.delete("/openapi/v1/source-credentials/corp-git")) == {
        "deleted": True
    }
    assert _ok(client.delete("/openapi/v1/source-credentials/corp-git")) == {
        "deleted": True
    }
    resp = client.get("/openapi/v1/source-credentials/corp-git")
    assert resp.status_code == 404


def test_fail_closed_refusal_is_503(service):
    sqlite = InMemorySqlite()
    fail_closed_service = SourceCredentialService(
        SourceCredentialRepository(sqlite), TokenVault(""), fail_closed=True
    )

    class _M(Module):
        def configure(self, binder):
            binder.bind(SourceCredentialServiceProtocol, to=fail_closed_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    c = user_scoped_client(app, "u1")

    resp = c.put("/openapi/v1/source-credentials/corp-git", json=A_BODY)
    assert resp.status_code == 503
    # 库里没有半行明文。
    assert fail_closed_service._repository.list() == []


def test_deleted_reference_binding_fails_by_name(service, client):
    client.put("/openapi/v1/source-credentials/corp-git", json=A_BODY)
    binding = service.binding(name="corp-git")
    client.delete("/openapi/v1/source-credentials/corp-git")
    with pytest.raises(CredentialNotFoundError, match="corp-git"):
        binding.headers_for(A_BODY["allowed_prefixes"][0])


def test_master_key_unavailable_maps_to_503_envelope(client):
    # 单独类映射断言:错误类型与 status/message 对齐已注册表。
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        ENVELOPE_ERRORS,
    )

    assert ENVELOPE_ERRORS[MasterKeyUnavailableError] == (
        503,
        "Source credential storage is unavailable",
    )
