"""Cross-tenant isolation for ``ac_bot_mcp_call_config`` (Track A, Stage 5).

These rows record, per bot and per MCP server, whether the server is called as
the bot's *owner* or as the *caller*. They hang off a bot, which Stage 1 already
isolates — but the two aggregate reads query this table by ``bot_pk`` alone and
never mention a bot record, so Stage 1's guard does not reach them:

* ``list_draft_call_types`` (``plugins/caller_identity_repository.py:279``)
* the call-type rollup ``_aggregate`` (``:302``)

This is a second independent barrier rather than the closing of a live hole:
``bot_pk`` is ``ac_bots.id``, a global primary key, and every call site sources
it from a tenant-guarded bot lookup, so there is no reachable cross-tenant
``bot_pk``. The tests below therefore drive the repository directly with a
foreign ``bot_pk`` — the shape a future careless call site would produce.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollabLockModel
from agentclaw.community.core.caller_identity.models import (
    BotMcpCallConfigModel,
    McpCallType,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.avernet_tenant_guard import CrossTenantInsertError
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
    transactional_orm_session = orm_session


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'caller.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    BotMcpCallConfigModel.__table__.create(engine)
    BotCollabLockModel.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return CallerIdentityRepository(db)


def _add_row(db, *, tenant, bot_pk, server_code, call_type="caller"):
    """Insert one override under ``tenant``, letting the insert guard stamp it."""
    with avernet_tenant_scope(tenant):
        with db.orm_session() as s:
            s.add(
                BotMcpCallConfigModel(
                    bot_pk=bot_pk,
                    server_code=server_code,
                    engine_type="openclaw",
                    call_type=call_type,
                    modifier_id="emp1",
                    env=get_current_env(),
                )
            )


@pytest.fixture
def two_tenant_rows(db):
    """Two tenants, each with an override — distinct bot_pks, as production has.

    ``bot_pk`` is a global primary key, so tenants never share one. The point is
    that a read given the *other* tenant's bot_pk must still come back empty.
    """
    _add_row(db, tenant="tenant-a", bot_pk=1, server_code="mcp.third.weather")
    _add_row(db, tenant="tenant-b", bot_pk=2, server_code="mcp.third.weather")
    return db


# ── the two aggregate reads ─────────────────────────────────────────


def test_list_draft_call_types_is_tenant_scoped(repo, two_tenant_rows):
    """The read that never mentions a bot record is still confined."""
    with avernet_tenant_scope("tenant-a"):
        assert repo.list_draft_call_types(1, "openclaw") == {
            "mcp.third.weather": McpCallType.CALLER
        }
        # tenant-b's bot_pk, handed to a tenant-a request: nothing.
        assert repo.list_draft_call_types(2, "openclaw") == {}

    with avernet_tenant_scope("tenant-b"):
        assert repo.list_draft_call_types(2, "openclaw") == {
            "mcp.third.weather": McpCallType.CALLER
        }
        assert repo.list_draft_call_types(1, "openclaw") == {}


def test_aggregate_rollup_is_tenant_scoped(repo, two_tenant_rows, db):
    """The call-type rollup must not see another tenant's CALLER override.

    Without the guard a tenant-a request reading tenant-b's bot_pk resolves to
    CALLER — the wrong execution identity for an MCP call.
    """
    with db.orm_session() as session:
        with avernet_tenant_scope("tenant-a"):
            assert (
                repo._aggregate(
                    session,
                    bot_pk=2,
                    engine_type="openclaw",
                    effective_server_codes={"mcp.third.weather"},
                    env=get_current_env(),
                )
                is McpCallType.OWNER
            )
        with avernet_tenant_scope("tenant-b"):
            assert (
                repo._aggregate(
                    session,
                    bot_pk=2,
                    engine_type="openclaw",
                    effective_server_codes={"mcp.third.weather"},
                    env=get_current_env(),
                )
                is McpCallType.CALLER
            )


def test_own_tenant_still_sees_its_overrides(repo, db):
    """The guard must not over-filter."""
    _add_row(db, tenant="tenant-a", bot_pk=7, server_code="mcp.a")
    _add_row(db, tenant="tenant-a", bot_pk=7, server_code="mcp.b", call_type="owner")

    with avernet_tenant_scope("tenant-a"):
        assert repo.list_draft_call_types(7, "openclaw") == {
            "mcp.a": McpCallType.CALLER,
            "mcp.b": McpCallType.OWNER,
        }


# ── inserts ─────────────────────────────────────────────────────────


def test_insert_stamps_the_current_tenant(db):
    _add_row(db, tenant="tenant-b", bot_pk=3, server_code="mcp.third.weather")

    with db.orm_session() as s:
        row = (
            s.query(BotMcpCallConfigModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "tenant-b"


def test_insert_outside_any_request_stamps_the_default_tenant(db):
    with db.orm_session() as s:
        s.add(
            BotMcpCallConfigModel(
                bot_pk=4,
                server_code="mcp.third.weather",
                engine_type="openclaw",
                call_type="caller",
                modifier_id="emp1",
                env=get_current_env(),
            )
        )

    with db.orm_session() as s:
        row = (
            s.query(BotMcpCallConfigModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "teamclaw"


def test_explicit_conflicting_tenant_insert_raises(db):
    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(CrossTenantInsertError, match="BotMcpCallConfigModel"):
            with db.orm_session() as s:
                s.add(
                    BotMcpCallConfigModel(
                        bot_pk=5,
                        server_code="mcp.third.weather",
                        engine_type="openclaw",
                        call_type="caller",
                        modifier_id="emp1",
                        env=get_current_env(),
                        avernet_tenant="tenant-a",  # != current context
                    )
                )
                s.flush()


# ── writes ──────────────────────────────────────────────────────────


def test_cross_tenant_delete_is_a_noop(db):
    """The OWNER path deletes a row; it must not reach another tenant's."""
    _add_row(db, tenant="tenant-a", bot_pk=6, server_code="mcp.third.weather")

    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            deleted = (
                s.query(BotMcpCallConfigModel)
                .filter(BotMcpCallConfigModel.bot_pk == 6)
                .delete(synchronize_session=False)
            )
            assert deleted == 0

    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            assert s.query(BotMcpCallConfigModel).count() == 1
