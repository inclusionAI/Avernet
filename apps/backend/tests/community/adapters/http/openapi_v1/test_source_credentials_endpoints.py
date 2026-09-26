"""Endpoint tests for ``/openapi/v1/bots/source-credentials`` (W3, #1471).

A minimal FastAPI app hosts the group's router with the caller principal
overridden and the real service graph over an in-memory SQLite repository
(the fail-closed and all-or-nothing refusals are storage claims — a mock
would confirm them vacuously). Prefix-boundary semantics live in the
policy suite; these pin the wire contract.

This is an application-operated surface: the caller principal names a
calling application (the stand-in carries ``app_id``), with a user
identity riding along only for audit attribution — so the tests inject
exactly those shapes, including the app-only one the edge lets through.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.adapters.http.openapi_v1.source_credentials import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
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

_BASE = "/openapi/v1/bots/source-credentials"

APP_ID = 7
OTHER_APP_ID = 8

A_BODY = {
    "type": "header",
    "header_name": "PRIVATE-TOKEN",
    "secret": "Bearer hush-token",
    "allowed_prefixes": ["https://git.example/team/content"],
}


@dataclass
class _Principal:
    """A stand-in the group's own helpers read attribute-wise.

    ``caller_app_id`` reads ``.app_id``; ``caller_owner_id`` reads
    ``.user_id``; ``app-only`` is the ``user_id=None`` shape the edge
    admits and ``caller_owner_id`` refuses to answer.
    """

    app_id: int
    user_id: str | None = "u1"


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
    """The app-on-behalf-of-user caller: app + riding user identity."""

    return _client(service, _Principal(APP_ID))


def _client(service, principal):
    class _M(Module):
        def configure(self, binder):
            binder.bind(SourceCredentialServiceProtocol, to=service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: principal
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return TestClient(app)


def _ok(resp, code=200000):
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["code"] == code, body
    return body["data"]


# --- PUT/GET round trip -----------------------------------------------------


def test_put_answers_masked_and_get_reads_it_back(client, service):
    data = _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))
    assert data["name"] == "corp-git"
    assert data["has_secret"] is True
    assert data["owner_app_id"] == APP_ID
    assert "Bearer hush-token" not in str(data)
    assert "secret" not in data  # masked 形态的键集合里根本没有它

    got = _ok(client.get(f"{_BASE}/corp-git"))
    assert got["header_name"] == "PRIVATE-TOKEN"
    assert got["allowed_prefixes"] == A_BODY["allowed_prefixes"]
    assert "hush-token" not in str(got)

    # 存储侧:密文,不是明文(公开面之外,服务真库断言)。
    row = service._repository.get(name="corp-git")
    assert row.secret_ciphertext.startswith(CIPHER_PREFIX)
    assert "hush-token" not in row.secret_ciphertext
    # 审计:principal 组合的 actor 已落列——app 优先,带则挂 on-behalf-of。
    assert row.modifier == f"app:{APP_ID}:on-behalf-of:u1"


def test_app_only_caller_registers_with_no_on_behalf_of(service):
    """The app-only shape the edge admits: actor is the application alone."""

    c = _client(service, _Principal(APP_ID, user_id=None))
    _ok(c.put(f"{_BASE}/corp-git", json=A_BODY))
    row = service._repository.get(name="corp-git")
    assert row.modifier == f"app:{APP_ID}"
    # 读取对租户内每个 app 开放——与谁创建无关。
    assert _ok(c.get(f"{_BASE}/corp-git"))["name"] == "corp-git"


def test_caller_without_an_app_is_refused(service):
    """No app identity → 401: ownership cannot be attributed to nobody.

    The edge requires an app credential; a caller that has nonetheless
    arrived without one is a gateway misconfiguration, answered with the
    same 401 an unverified caller gets.
    """

    class _NoApp:
        user_id = "u1"
        app_id = None

    c = _client(service, _NoApp())
    resp = c.put(f"{_BASE}/corp-git", json=A_BODY)
    assert resp.status_code == 401
    assert service._repository.get(name="corp-git") is None


def test_gets_404_masked(client):
    resp = client.get(f"{_BASE}/ghost")
    assert resp.status_code == 404
    assert resp.json()["data"] is None


def test_put_422_refuses_reserved_and_bad_prefixes(client):
    resp = client.put(
        f"{_BASE}/corp-git",
        json={**A_BODY, "type": "basic"},
    )
    assert resp.status_code == 422

    resp = client.put(
        f"{_BASE}/corp-git",
        json={**A_BODY, "type": "digest"},
    )
    assert resp.status_code == 422

    resp = client.put(
        f"{_BASE}/corp-git",
        json={**A_BODY, "allowed_prefixes": []},
    )
    assert resp.status_code == 422
    # 未写入。
    assert _ok(client.get(_BASE)) == []


def test_put_422_for_invalid_header_name(client):
    resp = client.put(
        f"{_BASE}/corp-git",
        json={**A_BODY, "header_name": "Not A Header"},
    )
    assert resp.status_code == 422


# --- ownership: rotation and delete are the owner's alone --------------------


def test_rotation_by_the_owner_replaces_in_place(client, service):
    _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))
    _ok(
        client.put(
            f"{_BASE}/corp-git",
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
    # 归属仍是创建者:轮换不改 owner。
    assert rows[0].owner_app_id == APP_ID


def test_rotation_by_another_application_is_403_and_stores_nothing(client, service):
    """A re-PUT replaces the whole row every citation of the name rides on —
    so it is the owner's alone, refused before storage."""
    _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))

    other = _client(service, _Principal(OTHER_APP_ID))
    resp = other.put(
        f"{_BASE}/corp-git",
        json={**A_BODY, "secret": "Bearer hijack"},
    )
    assert resp.status_code == 403
    assert resp.json()["data"] is None

    stored = service._repository.get(name="corp-git")
    binding = service.binding(name="corp-git")
    assert (
        binding.headers_for(A_BODY["allowed_prefixes"][0])["PRIVATE-TOKEN"]
        != "Bearer hijack"
    )
    assert stored.modifier == f"app:{APP_ID}:on-behalf-of:u1"


def test_delete_by_another_application_is_403(service):
    _ok(_client(service, _Principal(APP_ID)).put(f"{_BASE}/corp-git", json=A_BODY))

    other = _client(service, _Principal(OTHER_APP_ID))
    resp = other.delete(f"{_BASE}/corp-git")
    assert resp.status_code == 403
    assert service._repository.get(name="corp-git") is not None


def test_delete_is_idempotent_and_404_afterwards(client):
    _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))
    assert _ok(client.delete(f"{_BASE}/corp-git")) == {"deleted": True}
    assert _ok(client.delete(f"{_BASE}/corp-git")) == {"deleted": True}
    resp = client.get(f"{_BASE}/corp-git")
    assert resp.status_code == 404


def test_inventory_is_readable_by_every_tenant_app(client, service):
    """名字是租户共享的引用命名空间:读取(含清单)对所有 app 开放。"""
    _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))
    other = _client(service, _Principal(OTHER_APP_ID))
    assert [item["name"] for item in _ok(other.get(_BASE))] == ["corp-git"]
    assert _ok(other.get(f"{_BASE}/corp-git"))["owner_app_id"] == APP_ID


# --- fail-closed storage -----------------------------------------------------


def test_fail_closed_refusal_is_503(service):
    sqlite = InMemorySqlite()
    fail_closed_service = SourceCredentialService(
        SourceCredentialRepository(sqlite), TokenVault(""), fail_closed=True
    )
    c = _client(fail_closed_service, _Principal(APP_ID))

    resp = c.put(f"{_BASE}/corp-git", json=A_BODY)
    assert resp.status_code == 503
    # 库里没有半行明文。
    assert fail_closed_service._repository.list() == []


def test_deleted_reference_binding_fails_by_name(service, client):
    _ok(client.put(f"{_BASE}/corp-git", json=A_BODY))
    binding = service.binding(name="corp-git")
    _ok(client.delete(f"{_BASE}/corp-git"))
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


def test_not_owned_maps_to_403_envelope(client):
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        ENVELOPE_ERRORS,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.errors import (
        CredentialNotOwnedError,
    )

    assert ENVELOPE_ERRORS[CredentialNotOwnedError] == (
        403,
        "Source credential is owned by another application",
    )
