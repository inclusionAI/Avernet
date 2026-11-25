"""SqliteDB — local-mode DatabasePlugin implementation.

In-memory only. The local DatabasePlugin used to honour ``DATABASE_URL``
and fall back to ``sqlite:///./backend.db`` on disk; that path has been
removed. The engine is always an in-memory SQLite database with
``StaticPool`` so all connections in a process share the same DB. This
makes local mode side-effect free (no ``backend.db`` ever appears on
disk) and gives tests true per-process isolation when paired with
``reset_for_tests()``.

Because ``StaticPool`` exposes that one DBAPI connection to every local
Session, the DatabasePlugin serializes the full ``session()`` and
``orm_session()`` lifetimes with one process-wide reentrant lock. SQLite
transaction locks only coordinate separate connections; they cannot protect
two concurrent Sessions using this same connection.

Trade-off: ``./scripts/local_setup.sh --local`` no longer persists DB
state across backend restarts. Documented in the project ``CLAUDE.md``
"Running Modes" table.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///:memory:")


def _make_engine_and_session():
    """Create engine + SessionLocal for an in-memory SQLite DB.

    ``StaticPool`` reuses a single underlying connection across
    checkouts, which is required for ``:memory:`` because each connection
    otherwise gets its own private in-memory database.
    ``check_same_thread=False`` permits FastAPI's threaded request
    handling to share the connection.
    """
    engine = create_engine(
        _DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite does not enforce FOREIGN KEY constraints unless
    # ``PRAGMA foreign_keys=ON`` is set per-connection. The unified
    # config repository's ``delete_category`` is a single blind DELETE
    # that relies on the ``ac_config_item.parent_id`` FK's
    # ``ON DELETE CASCADE`` to remove child rows — exactly as prod
    # OceanBase does. This connect listener makes that cascade fire on
    # SQLite too, giving true prod parity without an extra statement.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, session_factory


# Module-level lazy singleton (created on first use)
_engine = None
_session_factory = None
_session_lock = threading.RLock()


def _get_session_factory():
    global _engine, _session_factory
    if _session_factory is None:
        _engine, _session_factory = _make_engine_and_session()
    return _session_factory


def get_session():
    """Return an unmanaged legacy/test Session; production must use the plugin."""
    factory = _get_session_factory()
    return factory()


# Unmanaged legacy/test callable matching the old db.py pattern. Production
# code must use SqliteDB.session()/orm_session() so the shared lock is held.
def SessionLocal():
    """Return an unmanaged legacy/test Session; production must use the plugin."""
    return _get_session_factory()()


# Snapshot of the first freshly bootstrapped in-memory schema in this process,
# keyed by ``schema_signature()``. ``create_all`` over ~280 tables costs
# ~150-200 ms, and the test profile builds a fresh in-memory database per test
# (``reset_for_tests`` + ``bootstrap``); copying this snapshot with SQLite's
# online backup API produces the identical schema in ~2 ms.
_schema_template: tuple[tuple, sqlite3.Connection] | None = None


def _is_in_memory(engine) -> bool:
    return engine.url.get_backend_name() == "sqlite" and engine.url.database in (
        None,
        "",
        ":memory:",
    )


def _bootstrap_schema(engine) -> None:
    """Create the schema on ``engine``, cloning a cached snapshot when possible.

    The snapshot is only copied into an *empty* in-memory database whose
    registered table layout matches the one the snapshot was taken from; any
    other case (a non-memory URL, a database that already holds tables, a
    changed layout) runs the real ``create_all``.
    """
    global _schema_template
    from agentclaw.community.core.schema import create_all, schema_signature

    if not _is_in_memory(engine):
        create_all(engine)
        return

    signature = schema_signature()
    raw = engine.raw_connection()
    try:
        target = raw.driver_connection
        empty = target.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        template = _schema_template
        if empty and template is not None and template[0] == signature:
            template[1].backup(target)
            return
    finally:
        raw.close()

    create_all(engine)
    if not empty:
        return
    snapshot = sqlite3.connect(":memory:", check_same_thread=False)
    raw = engine.raw_connection()
    try:
        raw.driver_connection.backup(snapshot)
    finally:
        raw.close()
    _schema_template = (signature, snapshot)


def reset_for_tests() -> None:
    """Dispose the cached engine and null the singletons.

    Call this between tests (or between injector builds) when you need
    a guaranteed-fresh in-memory database. Safe to call repeatedly,
    including before the singletons have ever been initialised.
    """
    global _engine, _session_factory
    with _session_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="SQLite :memory: — real SQL engine, in-process",
)
class SqliteDB(MockSeam, DatabasePlugin, LifecycleBase):
    """DatabasePlugin implementation for local mode (SQLite via SQLAlchemy)."""

    async def bootstrap(self) -> None:
        """Lifecycle hook — populate the ORM metadata and CREATE TABLE.

        The model registration and DDL themselves live in
        ``core/schema.py`` so the community plugin runs the identical
        bootstrap against a real store.

        Order matters: we resolve ``_get_session_factory`` *before*
        ``create_all`` so the TestingDatabaseModule's first-resolution
        ``reset_for_tests()`` (which disposes any prior engine) fires
        first. Otherwise ``create_all`` would write to an engine that's
        about to be wiped, and the next request would lazy-init a fresh
        empty one.
        """
        _get_session_factory()
        with _session_lock:
            _bootstrap_schema(_engine)

    @contextmanager
    def session(self):
        """Yield a SQLAlchemy Session."""
        with _session_lock:
            factory = _get_session_factory()
            db = factory()
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @contextmanager
    def orm_session(self):
        """Yield a SQLAlchemy Session that commits on clean exit.

        Unified ORM repositories use this instead of ``session()`` so
        writes persist without an explicit commit (parity with prod's
        ``AUTOCOMMIT``). ``session()`` is left unchanged for all other
        callers, so no other repository's persistence behaviour changes.
        """
        with _session_lock:
            factory = _get_session_factory()
            db = factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @contextmanager
    def transactional_orm_session(self):
        """Keep SQLite SAVEPOINTs and guarded reads inside the write UoW."""
        with self.orm_session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db
