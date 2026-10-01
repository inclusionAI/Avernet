"""The model-agnostic tenant guard: registration contract and multi-model reads.

Stage 5 generalizes the Stage 1 guard: instead of naming ``BotModel``, one
``do_orm_execute`` listener appends a ``with_loader_criteria`` option **per
registered model** to every ORM statement. Two things have to hold for that:

* an option naming an entity the statement does not touch must be a no-op —
  otherwise every ORM query in the process would change shape;
* a *second* model put through the registrar must actually be filtered and
  stamped, which is the whole point of the stage.

Everything here drives the **real** ``register_avernet_tenant_guard``, the real
``Session`` and the real ``avernet_tenant_scope``. An earlier draft attached a
hand-copied listener to a private ``Session`` subclass; it passed with the
production guard entirely disabled, so it proved nothing about the shipped code.

The toy models register at import and stay registered for the process. That is
harmless precisely because of the no-op property asserted below — their criteria
never reach a statement that does not touch their tables.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, event
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.avernet_tenant_guard import (
    CrossTenantInsertError,
    guarded_models,
    register_avernet_tenant_guard,
)

pytestmark = pytest.mark.integration


_Base = declarative_base()


class _Alpha(_Base):
    __tablename__ = "guard_alpha"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


class _Beta(_Base):
    __tablename__ = "guard_beta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


class _Parent(_Base):
    __tablename__ = "guard_parent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    children = relationship("_Child", back_populates="parent")


class _Child(_Base):
    __tablename__ = "guard_child"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("guard_parent.id"), nullable=False)
    name = Column(String(32), nullable=False)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    parent = relationship("_Parent", back_populates="children")


class _Unguarded(_Base):
    """Mapped, but declares no tenant column — must be refused registration."""

    __tablename__ = "guard_unguarded"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)


class _FakeTenantColumn(_Base):
    """``avernet_tenant`` is a plain value, not a mapped Column.

    ``hasattr`` cannot tell this apart from a real column; the mapper can.
    Registering it would make the read guard emit ``WHERE 1 = 1``.
    """

    __tablename__ = "guard_fake_tenant"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    avernet_tenant = "teamclaw"


register_avernet_tenant_guard(_Alpha)
register_avernet_tenant_guard(_Beta)
register_avernet_tenant_guard(_Parent)
register_avernet_tenant_guard(_Child)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)

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

    return session, statements


def _seed(session):
    """Seed both tenants. Each row is inserted under its own scope so the real
    insert guard does the stamping — no hand-set tenant values."""
    for tenant, suffix in (("tenant-a", "own"), ("tenant-b", "other")):
        with avernet_tenant_scope(tenant):
            with session() as s:
                s.add_all(
                    [
                        _Alpha(name=f"a-{suffix}"),
                        _Beta(name=f"b-{suffix}"),
                    ]
                )
    with session() as s:
        s.add_all([_Unguarded(name="u1"), _Unguarded(name="u2")])


# ── the no-op property the one-listener design rests on ─────────────


def test_absent_model_criteria_do_not_change_results(db):
    """Beta's criteria must not perturb a query touching only Alpha."""
    session, _ = db
    _seed(session)

    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            assert [r.name for r in s.query(_Alpha).all()] == ["a-own"]

    with avernet_tenant_scope("tenant-b"):
        with session() as s:
            assert [r.name for r in s.query(_Alpha).all()] == ["a-other"]


def test_absent_model_criteria_do_not_change_sql(db):
    """The emitted SQL names only the queried table — no join, no extra term."""
    session, statements = db
    _seed(session)

    statements.clear()
    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            s.query(_Alpha).all()

    selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    emitted = selects[0]
    assert "guard_alpha" in emitted
    assert "guard_beta" not in emitted
    assert "ac_bots" not in emitted
    # Alpha's own criteria did apply — the guard working, not silence. Assert on
    # the WHERE clause alone: the column also appears in the SELECT list, so a
    # whole-statement count would not say what we mean.
    where = emitted.split("WHERE", 1)[1].strip()
    assert where == "guard_alpha.avernet_tenant = ?"


# ── a second registered model is independently guarded ──────────────


def test_second_registered_model_is_read_guarded(db):
    """The Stage 5 point: registering another model actually filters it."""
    session, _ = db
    _seed(session)

    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            assert [r.name for r in s.query(_Beta).all()] == ["b-own"]

    with avernet_tenant_scope("tenant-b"):
        with session() as s:
            assert [r.name for r in s.query(_Beta).all()] == ["b-other"]


def test_second_registered_model_is_insert_stamped(db):
    """Inserts on a non-Bot model are stamped with no explicit tenant at the
    call site."""
    session, _ = db

    with avernet_tenant_scope("tenant-c"):
        with session() as s:
            s.add(_Beta(name="b-c"))

    with avernet_tenant_scope("tenant-c"):
        with session() as s:
            row = s.query(_Beta).one()
            assert row.name == "b-c"
            assert row.avernet_tenant == "tenant-c"


def test_second_registered_model_rejects_cross_tenant_insert(db):
    """An explicit conflicting tenant is refused, not silently rewritten."""
    session, _ = db

    with avernet_tenant_scope("tenant-c"):
        with pytest.raises(CrossTenantInsertError, match="_Beta"):
            with session() as s:
                s.add(_Beta(name="evil", avernet_tenant="tenant-d"))
                s.flush()


def test_cross_tenant_write_is_a_noop_on_a_second_model(db):
    """Update/delete against another tenant's row touch nothing."""
    session, _ = db
    _seed(session)

    with avernet_tenant_scope("tenant-b"):
        with session() as s:
            assert s.query(_Beta).filter(_Beta.name == "b-own").delete() == 0
            assert (
                s.query(_Beta)
                .filter(_Beta.name == "b-own")
                .update({"name": "HACKED"})
                == 0
            )

    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            assert [r.name for r in s.query(_Beta).all()] == ["b-own"]


def test_lazy_loaded_relationship_is_tenant_filtered(db):
    """A parent's relationship must not expose a child from another tenant."""
    session, _ = db

    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            parent = _Parent(name="parent-a")
            s.add(parent)
            s.flush()
            parent_id = parent.id
            s.add(_Child(parent_id=parent_id, name="child-a"))

    # This is a malformed cross-tenant association, but the ORM guard must
    # still prevent it from becoming visible through a lazy relationship load.
    with avernet_tenant_scope("tenant-b"):
        with session() as s:
            s.add(_Child(parent_id=parent_id, name="child-b"))

    with avernet_tenant_scope("tenant-a"):
        with session() as s:
            parent = s.query(_Parent).filter_by(name="parent-a").one()
            assert [child.name for child in parent.children] == ["child-a"]


def test_unregistered_model_is_untouched(db):
    """A model with no tenant column is not reached by the listener."""
    session, _ = db
    _seed(session)

    with avernet_tenant_scope("tenant-b"):
        with session() as s:
            assert {r.name for r in s.query(_Unguarded).all()} == {"u1", "u2"}


# ── the registrar's own contract ────────────────────────────────────


def test_model_without_tenant_column_is_rejected():
    """Guarding a model that declares no tenant column is a programming error."""
    with pytest.raises(TypeError, match="avernet_tenant"):
        register_avernet_tenant_guard(_Unguarded)
    assert _Unguarded not in guarded_models()


def test_model_with_unmapped_tenant_attribute_is_rejected():
    """A plain-value ``avernet_tenant`` must not register.

    ``hasattr`` cannot distinguish it from a mapped column. If it registered,
    ``getattr(model, "avernet_tenant") == tenant`` would evaluate to a Python
    bool and the guard would emit ``WHERE 1 = 1`` — every row of every tenant,
    silently.
    """
    with pytest.raises(TypeError, match="mapped"):
        register_avernet_tenant_guard(_FakeTenantColumn)
    assert _FakeTenantColumn not in guarded_models()


def test_double_registration_is_harmless(db):
    """Re-registering a model leaves the registry and its behavior unchanged.

    Note on strength: ``_GUARDED_MODELS`` is a dict, so a duplicate key is
    structurally impossible, and SQLAlchemy dedupes an identical
    ``(target, identifier, fn)`` listener triple. This asserts the observable
    contract — registered once, still filtering, still stamping — rather than
    claiming to falsify the early return.
    """
    session, _ = db
    before = guarded_models()

    register_avernet_tenant_guard(_Alpha)
    register_avernet_tenant_guard(_Alpha)

    assert guarded_models() == before
    assert guarded_models().count(_Alpha) == 1

    with avernet_tenant_scope("tenant-e"):
        with session() as s:
            s.add(_Alpha(name="a-e"))
    with avernet_tenant_scope("tenant-e"):
        with session() as s:
            rows = s.query(_Alpha).all()
            assert [r.name for r in rows] == ["a-e"]
            assert rows[0].avernet_tenant == "tenant-e"


def test_bot_model_is_registered():
    """The Stage 1 model goes through the same registrar as everything else."""
    from agentclaw.community.plugin_api.models import BotModel

    assert BotModel in guarded_models()


def test_read_guard_tolerates_registration_during_iteration():
    """A registration landing mid-listener must not abort the in-flight query.

    Model modules are routinely imported lazily inside functions, and plenty of
    DB work runs on background threads under ``bind_current_avernet_tenant``. So
    a registration can land while another thread is inside the listener.
    Iterating the live registry would raise "dictionary changed size during
    iteration" from inside ``do_orm_execute`` — fail-closed, but a hard 500 on
    an arbitrary query.

    Driven deterministically rather than with racing threads: the statement's
    ``options()`` performs the registration, which is exactly the interleaving
    that breaks a live-dict iteration.
    """
    from agentclaw.community.utils import avernet_tenant_guard as guard

    class _LateModel(_Base):
        __tablename__ = "guard_late"

        id = Column(Integer, primary_key=True, autoincrement=True)
        avernet_tenant = Column(
            String(64), nullable=False, server_default="teamclaw"
        )

    fired: list[bool] = []

    class _Statement:
        def options(self, *_args, **_kwargs):
            if not fired:
                fired.append(True)
                register_avernet_tenant_guard(_LateModel)
            return self

    state = SimpleNamespace(
        is_column_load=False,
        is_relationship_load=False,
        is_select=True,
        is_update=False,
        is_delete=False,
        execution_options={},
        statement=_Statement(),
    )

    guard._read_guard(state)  # must not raise RuntimeError

    assert fired, "the interleaving under test never happened"
    assert _LateModel in guarded_models()
