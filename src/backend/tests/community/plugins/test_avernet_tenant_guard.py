"""The tenant read guard must be inert for models a statement does not touch.

Stage 5 generalizes the Stage 1 guard: instead of naming ``BotModel``, one
``do_orm_execute`` listener appends a ``with_loader_criteria`` option **per
registered model** to every ORM statement. That design is only safe if an
option naming an entity the statement does not touch is a no-op — if it instead
forced a join, every ORM query in the process would change shape.

These started as the spike gating ``plan.md``'s one-listener design and are kept
as regression tests, the same way Stage 1 kept its ``Query.update()`` spike.

Toy models on a private registry, and a dedicated ``Session`` subclass, so the
listener under test cannot leak into the rest of the suite.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, Integer, String, create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker, with_loader_criteria

pytestmark = pytest.mark.integration


_Base = declarative_base()


class _Alpha(_Base):
    __tablename__ = "spike_alpha"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


class _Beta(_Base):
    __tablename__ = "spike_beta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


class _Gamma(_Base):
    __tablename__ = "spike_gamma"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


class _Unguarded(_Base):
    """A model that carries no tenant column and is never registered."""

    __tablename__ = "spike_unguarded"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)


_GUARDED = (_Alpha, _Beta, _Gamma)


class _SpikeSession(Session):
    """Dedicated subclass so the listener does not touch the global Session."""


@event.listens_for(_SpikeSession, "do_orm_execute")
def _read_guard(state) -> None:
    """The Stage 5 listener shape: one criteria option per guarded model."""
    if state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    for model in _GUARDED:
        state.statement = state.statement.options(
            with_loader_criteria(
                model,
                model.avernet_tenant == _current_tenant[0],
                include_aliases=True,
            )
        )


# A plain mutable holder rather than the real ContextVar — this file is about
# SQLAlchemy's behavior, not about how the tenant is carried.
_current_tenant = ["tenant-a"]


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'spike.db'}")
    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=_SpikeSession, autoflush=False)

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    @contextmanager
    def session():
        s = factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    yield session, statements
    _current_tenant[0] = "tenant-a"


def _seed(session):
    with session() as s:
        s.add_all(
            [
                _Alpha(name="a-own", avernet_tenant="tenant-a"),
                _Alpha(name="a-other", avernet_tenant="tenant-b"),
                _Beta(name="b-own", avernet_tenant="tenant-a"),
                _Beta(name="b-other", avernet_tenant="tenant-b"),
                _Unguarded(name="u1"),
                _Unguarded(name="u2"),
            ]
        )


def test_absent_model_criteria_do_not_change_results(db):
    """Criteria for Beta and Gamma must not perturb a query touching only Alpha."""
    session, _ = db
    _seed(session)

    _current_tenant[0] = "tenant-a"
    with session() as s:
        assert [r.name for r in s.query(_Alpha).all()] == ["a-own"]

    _current_tenant[0] = "tenant-b"
    with session() as s:
        assert [r.name for r in s.query(_Alpha).all()] == ["a-other"]


def test_absent_model_criteria_do_not_change_sql(db):
    """The emitted SQL must name only the queried table — no join, no extra term."""
    session, statements = db
    _seed(session)

    statements.clear()
    _current_tenant[0] = "tenant-a"
    with session() as s:
        s.query(_Alpha).all()

    selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    emitted = selects[0]
    assert "spike_alpha" in emitted
    assert "spike_beta" not in emitted
    assert "spike_gamma" not in emitted
    # Alpha's own criteria did apply — this is the guard working, not silence.
    # Assert on the WHERE clause alone: ``avernet_tenant`` also appears in the
    # SELECT list, so a whole-statement count would not say what we mean.
    where = emitted.split("WHERE", 1)[1].strip()
    assert where == "spike_alpha.avernet_tenant = ?"


def test_unregistered_model_is_untouched(db):
    """A model with no tenant column is not reached by the listener."""
    session, _ = db
    _seed(session)

    _current_tenant[0] = "tenant-b"
    with session() as s:
        assert {r.name for r in s.query(_Unguarded).all()} == {"u1", "u2"}


def test_guard_applies_to_each_registered_model_independently(db):
    """Registering three models guards all three, not just the first."""
    session, _ = db
    _seed(session)

    _current_tenant[0] = "tenant-a"
    with session() as s:
        assert [r.name for r in s.query(_Beta).all()] == ["b-own"]

    _current_tenant[0] = "tenant-b"
    with session() as s:
        assert [r.name for r in s.query(_Beta).all()] == ["b-other"]


# ── the registrar's own contract ────────────────────────────────────


def test_registration_is_idempotent():
    """A re-import must not double-register a model."""
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.utils.avernet_tenant_guard import (
        guarded_models,
        register_avernet_tenant_guard,
    )

    before = guarded_models()
    assert BotModel in before

    register_avernet_tenant_guard(BotModel)
    register_avernet_tenant_guard(BotModel)

    after = guarded_models()
    assert after == before
    assert after.count(BotModel) == 1


def test_model_without_tenant_column_is_rejected():
    """Guarding a model that declares no tenant column is a programming error."""
    from agentclaw.community.utils.avernet_tenant_guard import (
        register_avernet_tenant_guard,
    )

    with pytest.raises(TypeError, match="avernet_tenant"):
        register_avernet_tenant_guard(_Unguarded)
