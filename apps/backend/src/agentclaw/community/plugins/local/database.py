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
        from agentclaw.community.core.schema import create_all

        _get_session_factory()
        create_all(_engine)

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

    def transactional_orm_session(self):
        """Reuse the existing local commit/rollback transaction."""
        return self.orm_session()
