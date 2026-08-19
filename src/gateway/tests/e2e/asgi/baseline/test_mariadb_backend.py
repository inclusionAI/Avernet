"""E2E ASGI: comprehensive DB-backed access against a live MariaDB.

These tests exercise every gateway-owned database access path over the ASGI
transport and through the DI-resolved ORM registers, against a live MariaDB via
the ``e2e-mariadb`` overlay:

    just test-e2e-mariadb

``configs/overlays/e2e-mariadb.yaml`` sets ``create_schema: true``, so the
gateway auto-provisions its identity tables (``avernet_application``,
``avernet_tenant``, ``avernet_access_key_token``). ``bcs_bots`` is excluded by
design (bcs-owned), so bot-registry reads are not exercised here.

Docker/MariaDB availability is probed up front; every test is SKIPPED with an
informative message when the live backend is not reachable (e.g. no Docker
daemon or no local ``mariadb:11`` container on ``MARIADB_HOST:MARIADB_PORT``).
The ``just test-e2e-mariadb`` target tears the container down after the run.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

_MARIADB_HOST = os.environ.get("MARIADB_HOST", "127.0.0.1")
_MARIADB_PORT = int(os.environ.get("MARIADB_PORT", "33306"))
_MARIADB_DATABASE = os.environ.get("MARIADB_DATABASE", "gateway_test")
_MARIADB_USER = os.environ.get("MARIADB_USER", "gateway")
_MARIADB_PASSWORD = os.environ.get("MARIADB_PASSWORD", "gatewaypass")


def _signing_key() -> str:
    """Community env-var signing key name (configs/application.yaml)."""
    return "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE"


def _probe_mariadb() -> str | None:
    """Return a skip reason if a live MariaDB backend cannot be reached.

    Uses the driver the gateway itself uses (``mysql.connector``) against the
    published host port, so it reflects exactly what the app can reach — no
    nested-container networking that may not see the host port.
    """
    if not shutil.which("docker"):
        return "docker is not installed on this host"
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return "the docker daemon is not available"

    try:
        import mysql.connector as _connector

        conn = _connector.connect(
            host=_MARIADB_HOST,
            port=_MARIADB_PORT,
            database=_MARIADB_DATABASE,
            user=_MARIADB_USER,
            password=_MARIADB_PASSWORD,
            connection_timeout=5,
        )
        conn.close()
        return None
    except Exception as exc:  # noqa: BLE001 - any connect error means skip
        return f"cannot reach MariaDB at {_MARIADB_HOST}:{_MARIADB_PORT}: {exc}"


@pytest.fixture(scope="module", autouse=True)
def _require_live_mariadb() -> None:
    reason = _probe_mariadb()
    if reason is not None:
        pytest.skip(
            f"MariaDB e2e skipped: {reason}. Run via `just test-e2e-mariadb`, "
            "which starts a mariadb:11 container on port 33306 and tears it "
            "down afterwards."
        )


@pytest.fixture(autouse=True)
def _provision_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Access-key issuance signs with the principal signing key resolved from
    # ``{env_prefix}{NAME}_VALUE`` (configs/application.yaml).
    monkeypatch.setenv(_signing_key(), "e2e-mariadb-signing-key-32bytes!!")


def _db():
    from gateway.community.bootstrap import get_container

    db = get_container().plugins().database()
    if type(db).__name__ != "MariaDbOrmPlugin":
        pytest.fail(
            f"app resolved {type(db).__name__}, not MariaDbOrmPlugin — "
            "set SOFAPY_CONFIG_OVERLAY=e2e-mariadb when running these tests"
        )
    return db


def _register_app(client: TestClient, name: str, tenant: str = "mariadb-t", **extra):
    body = {
        "app_name": name,
        "owners": "org-1",
        "app_type": "assistant",
        "tenant": tenant,
        "creator": "e2e-admin",
    }
    body.update(extra)
    return client.post("/admin/apps", json=body)


def _issue_access_key(client: TestClient, key: str, tenant: str = "mariadb-t"):
    return client.post(
        "/admin/access-keys",
        json={
            "access_key": key,
            "tenant": tenant,
            "expire_at": "2027-01-01T00:00:00",
            "creator": "e2e-admin",
        },
    )


# ── App writes: /admin/access-keys ───────────────────────────────────────────


class TestAccessKeyWritePath:
    def test_access_key_persists(self, client: TestClient) -> None:
        _db()
        resp = _issue_access_key(client, "ak-e2e")
        assert resp.status_code == 201
        body = resp.json()
        token = body["token"]

        from gateway.community.core.access_key import AccessKeyRow

        with _db().orm_session() as session:
            row = session.query(AccessKeyRow).filter_by(access_key="ak-e2e").first()
            assert row is not None
            assert row.tenant == "mariadb-t"
            assert row.token == token
            assert row.creator == "e2e-admin"

    def test_access_key_validation(self, client: TestClient) -> None:
        assert (
            client.post(
                "/admin/access-keys",
                json={"access_key": "x"},  # missing tenant + expire_at
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/admin/access-keys",
                json={
                    "access_key": "x",
                    "tenant": "t",
                    "expire_at": "not-a-date",
                },
            ).status_code
            == 422
        )


# ── App writes: /admin/apps ──────────────────────────────────────────────────


class TestAppWritePath:
    def test_register_active_app_persists(self, client: TestClient) -> None:
        _db()
        resp = _register_app(client, "E2E App")
        assert resp.status_code == 201
        body = resp.json()
        assert isinstance(body["id"], int)
        assert body["status"] == "ACTIVE"

        from gateway.community.core.app import AppRow

        with _db().orm_session() as session:
            row = session.get(AppRow, body["id"])
            assert row is not None
            assert row.app_name == "E2E App"
            assert row.tenant == "mariadb-t"
            assert row.status == "ACTIVE"
            assert row.api_key_prefix == body["api_key"][:8]
            assert body["api_key"] not in (row.api_key_hash or "")

    def test_register_inactive_app_persists(self, client: TestClient) -> None:
        _db()
        resp = _register_app(client, "E2E Inactive", status="INACTIVE")
        assert resp.status_code == 201
        assert resp.json()["status"] == "INACTIVE"

    def test_unusable_statuses_rejected(self, client: TestClient) -> None:
        for status in ("REVOKED", "active", "Active", ""):
            assert (
                _register_app(client, f"Bad-{status}", status=status).status_code == 422
            )


# ── Read paths: credentials resolve against MariaDB ──────────────────────────


class TestDbReadPaths:
    def test_api_key_read_round_trip(self, client: TestClient) -> None:
        from gateway.community.core.app import AppRepository

        body = _register_app(client, "Reader App").json()
        api_key = body["api_key"]
        found = asyncio.run(AppRepository(_db()).find_app_by_credential(api_key))
        assert found is not None
        assert found.app_name == "Reader App"
        assert found.tenant == "mariadb-t"

    def test_wrong_api_key_soft_miss(self, client: TestClient) -> None:
        from gateway.community.core.app import APIKeyGenerator, AppRepository

        found = asyncio.run(AppRepository(_db()).find_app_by_credential("nope"))
        assert found is None
        # Wrong key for a real registered app also misses (kept apart from
        # `nope`, which is too short to carry a prefix).
        _register_app(client, "Wrong Key App")
        assert asyncio.run(AppRepository(_db()).find_app_by_credential("nope")) is None

    def test_inactive_app_read_rejected(self, client: TestClient) -> None:
        from gateway.community.core.app import AppRepository

        body = _register_app(client, "Inactive Reader", status="INACTIVE").json()
        found = asyncio.run(
            AppRepository(_db()).find_app_by_credential(body["api_key"])
        )
        assert found is None

    def test_short_credential_no_query(self, client: TestClient) -> None:
        from gateway.community.core.app import AppRepository

        assert asyncio.run(AppRepository(_db()).find_app_by_credential("short")) is None

    def test_legacy_jwt_read_path(self, client: TestClient) -> None:
        from gateway.community.core.app import AppRepository, AppRow

        legacy = "legacy-e2e.jwt.token"
        with _db().orm_session() as session:
            session.add(
                AppRow(
                    app_name="Legacy ACTIVE",
                    app_type="assistant",
                    token=legacy,
                    owners="org-legacy",
                    tenant="mariadb-t",
                    status="ACTIVE",
                )
            )
        found = asyncio.run(AppRepository(_db()).find_app_by_credential(legacy))
        assert found is not None
        assert found.app_name == "Legacy ACTIVE"

    def test_exists_prefix(self, client: TestClient) -> None:
        from gateway.community.core.app import AppRepository

        prefix = _register_app(client, "Prefix App").json()["api_key"][:8]
        repo = AppRepository(_db())
        assert asyncio.run(repo.exists_prefix(prefix)) is True
        assert asyncio.run(repo.exists_prefix("ZZZZZZZZ")) is False

    def test_access_key_lookup_round_trip(self, client: TestClient) -> None:
        from gateway.community.core.access_key import AccessKeyRepository

        token = _issue_access_key(client, "ak-reader").json()["token"]
        found = asyncio.run(AccessKeyRepository(_db()).find_access_key_by_token(token))
        assert found is not None
        assert found.access_key == "ak-reader"

    def test_access_key_unknown_soft_miss(self, client: TestClient) -> None:
        from gateway.community.core.access_key import AccessKeyRepository

        assert (
            asyncio.run(
                AccessKeyRepository(_db()).find_access_key_by_token("no-such-token")
            )
            is None
        )
