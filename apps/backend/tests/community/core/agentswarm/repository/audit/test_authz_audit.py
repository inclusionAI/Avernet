"""agentswarm authz + audit tests (task 12.7): boundary audit + tenant
isolation seams.

Two groups:

- ``TestAuditWriter`` — the real writer against an in-memory SQLite:
  rows land with the right scope/shape, reads are subject-filtered only,
  a broken session is swallowed, and the surface has no mutation methods
  (append-only by protocol shape, governance-style).
- ``TestRouterAuditTrail`` / ``TestAuditNeverBreaksRequests`` — the router
  records submit accept/reject (with admission reasons) and cancel outcomes
  best-effort through a recording writer double, and a raising writer can
  never change the caller's response.
"""
from __future__ import annotations

import contextlib

import injector
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import sessionmaker

from agentclaw.community.adapters.http.agentswarm.router import (
    register_exception_handlers, router,
)
from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol,
)
from agentclaw.community.core.agentswarm.repository.audit.impl import (
    REASONS, AgentSwarmAuditWriter, AuditAction,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.audit import model as am
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, RunEvents, SubmitRunRequest, TenantRunLimitExceeded,
)
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity


TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
STAFF_ID = "staff-9"


def _mk_run() -> dm.Run:
    return dm.Run(
        run_id=dm.RunId("run-1"),
        tenant=dm.TenantId(TENANT),
        goal="goal",
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.COMPLETED,
        started_at=dm.EpochMillis(1),
    )


class _TestDB:
    """Same self-managed orm_session shape as the other agentswarm tests."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextlib.contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _BrokenDB:
    @contextlib.contextmanager
    def orm_session(self):
        raise RuntimeError("db unavailable")
        yield None


class StubService:

    def __init__(self) -> None:
        self.run = _mk_run()
        self.submit_error: Exception | None = None

    async def submit_async(self, req: SubmitRunRequest) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        return self.run.run_id

    async def submit_sync(self, req: SubmitRunRequest) -> dm.Run:
        if self.submit_error is not None:
            raise self.submit_error
        return self.run

    def get_run(self, run_id: str, tenant: str) -> dm.Run | None:
        if run_id != self.run.run_id or tenant != TENANT:
            return None
        return self.run

    def list_runs(self, tenant: str, status: dm.RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> dm.RunPage:
        return dm.RunPage(items=(), total=0, page=page, page_size=page_size)

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        return RunEvents(
            agents=(), rounds=(), guards=(),
            run_status=self.run.status, terminal=self.run.status,
        )

    async def cancel(self, run_id: str, tenant: str) -> str:
        if run_id != self.run.run_id or tenant != TENANT:
            return "not_found"
        return self.run.status.value


class StubAuthPlugin:

    def __init__(self, tenant_id: str | None) -> None:
        self.tenant_id = tenant_id

    async def resolve_user_from_request(self, ctx) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            id="auth-user-9", operatorName="auth-user-9",
            outUserNo=STAFF_ID, tntInstId=self.tenant_id,
        )

    async def get_login_user(self, cookie: str, referer: str | None = None):
        raise NotImplementedError

    def is_operator_allowed(self, staff_id: str) -> bool:
        return True

    async def authorize_entity_access(self, ctx, requested_entity_id,
                                      requested_entity_type):
        return requested_entity_id, requested_entity_type


class RecordingAuditWriter:

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, *, tenant: str, actor: str, action: AuditAction,
               reason: str | None = None, run_id: str | None = None,
               source: str = "http") -> None:
        self.rows.append({
            "tenant": tenant, "actor": actor, "action": action.value,
            "reason": reason, "run_id": run_id, "source": source,
        })

    def list_by_run(self, run_id: str, tenant: str, limit: int = 50):
        raise AssertionError("router must not read the audit trail")

    def list_by_actor(self, actor: str, limit: int = 50):
        raise AssertionError("router must not read the audit trail")


class RaisingAuditWriter:

    def record(self, **kwargs) -> None:
        raise RuntimeError("writer exploded")


def _spec_body() -> dict:
    return {
        "tenant": TENANT,
        "goal": "map the incident",
        "agents": [{"name": "scout", "role": "research"}],
        "idempotency_key": "idem-audit",
    }


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    am.AcAgentSwarmAuditModel.__table__.create(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def writer(db_engine) -> AgentSwarmAuditWriter:
    return AgentSwarmAuditWriter(_TestDB(db_engine))



class TestAuditWriter:

    def test_record_persists_one_scoped_row(self, writer, db_engine):
        writer.record(
            tenant=TENANT, actor=STAFF_ID,
            action=AuditAction.SUBMIT_ACCEPTED, run_id="run-1",
        )
        factory = sessionmaker(bind=db_engine)
        with factory() as s:
            (row,) = s.execute(select(am.AcAgentSwarmAuditModel)).scalars()
            assert row.tenant == TENANT
            assert row.actor == STAFF_ID
            assert row.action == "submit_accepted"
            assert row.run_id == "run-1"
            assert row.reason is None
            assert row.source == "http"
            assert isinstance(row.env, str)
            assert row.gmt_create is not None

    def test_record_with_reason_persists_reason(self, writer, db_engine):
        writer.record(
            tenant=TENANT, actor=STAFF_ID,
            action=AuditAction.SUBMIT_REJECTED, reason="tenant_limit",
        )
        factory = sessionmaker(bind=db_engine)
        with factory() as s:
            (row,) = s.execute(select(am.AcAgentSwarmAuditModel)).scalars()
            assert row.action == "submit_rejected"
            assert row.reason == "tenant_limit"
            assert row.run_id is None

    def test_record_never_raises_on_session_failure(self):
        writer = AgentSwarmAuditWriter(_BrokenDB())
        writer.record(
            tenant=TENANT, actor=STAFF_ID, action=AuditAction.CANCEL_REQUESTED,
        )  # must not raise

    def test_list_by_run_is_tenant_scoped(self, writer):
        writer.record(tenant=TENANT, actor=STAFF_ID,
                      action=AuditAction.SUBMIT_ACCEPTED, run_id="run-1")
        writer.record(tenant=OTHER_TENANT, actor=STAFF_ID,
                      action=AuditAction.SUBMIT_ACCEPTED, run_id="run-1")
        writer.record(tenant=TENANT, actor=STAFF_ID,
                      action=AuditAction.SUBMIT_ACCEPTED, run_id="run-2")

        own = writer.list_by_run("run-1", TENANT)
        assert [(r.tenant, r.run_id) for r in own] == [(TENANT, "run-1")]
        foreign = writer.list_by_run("run-1", OTHER_TENANT)
        assert [(r.tenant, r.run_id) for r in foreign] == [(OTHER_TENANT, "run-1")]

    def test_list_by_actor_filters_by_actor(self, writer):
        writer.record(tenant=TENANT, actor="staff-a",
                      action=AuditAction.SUBMIT_ACCEPTED)
        writer.record(tenant=TENANT, actor="staff-b",
                      action=AuditAction.SUBMIT_ACCEPTED)
        rows = writer.list_by_actor("staff-a")
        assert [r.actor for r in rows] == ["staff-a"]

    def test_reads_refuse_unfiltered_subjects(self, writer):
        with pytest.raises(ValueError):
            writer.list_by_run("", TENANT)
        with pytest.raises(ValueError):
            writer.list_by_run("run-1", "")
        with pytest.raises(ValueError):
            writer.list_by_actor("")

    def test_no_mutation_methods_exist(self):
        writer = AgentSwarmAuditWriter.__new__(AgentSwarmAuditWriter)
        for forbidden in ("update", "delete", "remove", "edit", "purge"):
            assert not hasattr(writer, forbidden), (
                f"audit writer must stay append-only (found {forbidden})"
            )

    def test_read_limit_is_capped(self, writer):
        for i in range(3):
            writer.record(tenant=TENANT, actor=STAFF_ID,
                          action=AuditAction.SUBMIT_ACCEPTED, run_id=f"run-{i}")
        assert [r.run_id for r in writer.list_by_actor(STAFF_ID, limit=2)] == [
            "run-2", "run-1",
        ]

    def test_record_reasons_stay_in_the_documented_vocabulary(self, writer):
        for reason in ("tenant_limit", "global_limit", "queue_depth",
                       "config_missing", "not_found"):
            writer.record(tenant=TENANT, actor=STAFF_ID,
                          action=AuditAction.SUBMIT_REJECTED, reason=reason)
        with writer._db.orm_session() as s:
            s.expire_on_commit = False
            rows = s.execute(
                select(am.AcAgentSwarmAuditModel).order_by(
                    am.AcAgentSwarmAuditModel.id)
            ).scalars().all()
        assert {r.reason for r in rows} == set(REASONS)


class TestAuditModelShape:

    def test_table_name_and_index_scope(self):
        table = am.AcAgentSwarmAuditModel.__table__
        assert table.name == "ac_agent_swarm_audit"
        assert {c.name for c in table.columns} == {
            "id", "env", "tenant", "is_deleted", "gmt_create", "gmt_modified",
            "actor", "action", "reason", "run_id", "source",
        }
        for idx in table.indexes:
            names = {c.name for c in idx.columns}
            assert not idx.unique, "append-only audit has no unique keys"
            assert names >= {"env", "tenant"}, f"{idx.name} not tenant-scoped"

    def test_registered_in_all_models_and_metadata(self):
        from agentclaw.community.core.agentswarm.repository import registry as om
        from agentclaw.community.core.base import Base as CoreBase

        assert am.AcAgentSwarmAuditModel in om.ALL_MODELS
        assert "ac_agent_swarm_audit" in CoreBase.metadata.tables


class TestRouterAuditTrail:

    @pytest.fixture()
    def app_deps(self):
        stub = StubService()
        auth = StubAuthPlugin(tenant_id=TENANT)
        audit = RecordingAuditWriter()
        return stub, auth, audit

    def _client(self, app_deps) -> TestClient:
        stub, auth, audit = app_deps
        app = FastAPI()
        app.include_router(router)
        register_exception_handlers(app)
        inj = injector.Injector()
        inj.binder.bind(AgentSwarmServiceProtocol, to=stub)
        inj.binder.bind(AuthPlugin, to=auth)
        inj.binder.bind(AgentSwarmAuditWriter, to=audit)
        app.state.injector = inj
        return TestClient(app)

    def test_submit_accepted_records_row(self, app_deps):
        client = self._client(app_deps)
        stub, _, audit = app_deps
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert resp.status_code == 200
        (row,) = audit.rows
        assert row == {
            "tenant": TENANT, "actor": STAFF_ID,
            "action": "submit_accepted", "reason": None,
            "run_id": "run-1", "source": "http",
        }

    def test_submit_sync_accepted_records_row(self, app_deps):
        client = self._client(app_deps)
        _, _, audit = app_deps
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body(),
                           params={"wait": "true"})
        assert resp.status_code == 200
        (row,) = audit.rows
        assert row["action"] == "submit_accepted"
        assert row["run_id"] == "run-1"

    def test_submit_rejected_tenant_limit_records_reason(self, app_deps):
        client = self._client(app_deps)
        stub, _, audit = app_deps
        stub.submit_error = TenantRunLimitExceeded(tenant=TENANT, limit=8)
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert resp.status_code == 429
        (row,) = audit.rows
        assert row["action"] == "submit_rejected"
        assert row["reason"] == "tenant_limit"
        assert row["run_id"] is None

    def test_submit_rejected_config_missing_records_reason(self, app_deps):
        client = self._client(app_deps)
        stub, _, audit = app_deps
        stub.submit_error = AgentSwarmConfigError("llm config missing")
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert resp.status_code == 400
        (row,) = audit.rows
        assert row["action"] == "submit_rejected"
        assert row["reason"] == "config_missing"

    def test_cancel_known_run_records_row(self, app_deps):
        client = self._client(app_deps)
        _, _, audit = app_deps
        resp = client.post("/openapi/v1/agentswarm/runs/run-1/cancel",
                           params={"tenant": TENANT})
        assert resp.status_code == 200
        (row,) = audit.rows
        assert row["action"] == "cancel_requested"
        assert row["run_id"] == "run-1"
        assert row["reason"] is None

    def test_cancel_unknown_run_records_not_found_reason(self, app_deps):
        client = self._client(app_deps)
        _, _, audit = app_deps
        resp = client.post("/openapi/v1/agentswarm/runs/nope/cancel",
                           params={"tenant": TENANT})
        assert resp.status_code == 404
        (row,) = audit.rows
        assert row["action"] == "cancel_requested"
        assert row["reason"] == "not_found"

    def test_429_audit_row_svocabulary_is_documented(self, app_deps):
        client = self._client(app_deps)
        stub, _, audit = app_deps
        stub.submit_error = TenantRunLimitExceeded(tenant=TENANT, limit=8)
        client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert all(
            row["reason"] is None or row["reason"] in REASONS
            for row in audit.rows
        )

    def test_health_and_ready_do_not_record_rows(self, app_deps):
        client = self._client(app_deps)
        client.get("/openapi/v1/agentswarm/health")
        client.get("/openapi/v1/agentswarm/ready")
        _, _, audit = app_deps
        assert audit.rows == []


class TestAuditNeverBreaksRequests:

    def _client(self, stub, writer) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        register_exception_handlers(app)
        inj = injector.Injector()
        inj.binder.bind(AgentSwarmServiceProtocol, to=stub)
        inj.binder.bind(AuthPlugin, to=StubAuthPlugin(tenant_id=TENANT))
        inj.binder.bind(AgentSwarmAuditWriter, to=writer)
        app.state.injector = inj
        return TestClient(app)

    def test_raising_writer_does_not_change_submit_response(self):
        stub = StubService()
        client = self._client(stub, RaisingAuditWriter())
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert resp.status_code == 200
        assert resp.json() == {"run_id": "run-1"}

    def test_raising_writer_does_not_change_rejection_response(self):
        stub = StubService()
        stub.submit_error = TenantRunLimitExceeded(tenant=TENANT, limit=8)
        client = self._client(stub, RaisingAuditWriter())
        resp = client.post("/openapi/v1/agentswarm/runs", json=_spec_body())
        assert resp.status_code == 429