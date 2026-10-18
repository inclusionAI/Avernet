"""AgentSpec.config_manifest catalog roundtrip (ac_agent_swarm_run).

The encode side is ``a.__dict__`` and the decode side is ``AgentSpec(**c)``
(``repository/run/impl.py`` create/_to_domain), so a new dataclass field rides
both directions structurally. These tests pin it observably end to end: a
catalog entry submitted with a full manifest document must rehydrate with the
document byte-identical — never silently dropped — and catalog JSON written
before the field existed must still decode (D38 additive, backward-readable).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.run import RunRepository


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
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

    session = orm_session


@pytest.fixture()
def run_repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[om.AcAgentSwarmRunModel.__table__])
    yield RunRepository(_TestDB(engine))
    engine.dispose()


TENANT_A = dm.TenantId("tenant-a")

_DOCUMENT = (
    "schema_version: 1\n"
    "manifest:\n"
    "  identity:\n"
    "    - type: SOUL.md\n"
    "      content: 'be meticulous'\n"
    "  mcp: []\n"
    "  resources: []\n"
    "  skills: []\n"
    "  cli_tools: []\n"
    "script:\n"
    "  body: 'echo provisioned'\n"
)


def _mk_run() -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=TENANT_A,
        goal="test goal",
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


class TestCatalogConfigManifestRoundTrip:

    def test_catalog_config_manifest_round_trips(self, run_repo):
        run = _mk_run()
        run.agent_catalog = (
            dm.AgentSpec(name="r1", role="research"),
            dm.AgentSpec(name="r2", role="research",
                         config_manifest=_DOCUMENT),
        )
        run_repo.create(run)
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched.agent_catalog[0].config_manifest == ""
        assert fetched.agent_catalog[1].config_manifest == _DOCUMENT

    def test_catalog_old_row_without_config_manifest_decodes_default(
            self, run_repo):
        run = _mk_run()
        run_repo.create(run)
        with run_repo._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmRunModel).where(
                    om.AcAgentSwarmRunModel.run_id == run.run_id,
                )
            ).scalar_one()
            catalogs = json.loads(row.agent_catalog)
            assert "config_manifest" in catalogs[0]  # encode carries it…
            del catalogs[0]["config_manifest"]       # …simulate a legacy row
            row.agent_catalog = json.dumps(catalogs)
        fetched = run_repo.get(run.run_id, TENANT_A)
        assert fetched.agent_catalog[0].config_manifest == ""