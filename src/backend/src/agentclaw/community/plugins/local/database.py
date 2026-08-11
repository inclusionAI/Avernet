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
        """Lifecycle hook — populate ``Base.metadata`` and CREATE TABLE.

        Eagerly imports every ORM model so its ``class`` statement runs
        and SQLAlchemy's declarative metaclass registers a ``Table`` on
        ``Base.metadata``. Without this, ``create_all`` would only emit
        DDL for tables whose class was already transitively imported via
        the router chain — and method-level lazy imports in many
        repositories make that set non-deterministic. First request would
        hit ``no such table: ac_xxx``.

        Order matters: we resolve ``_get_session_factory`` *before*
        ``create_all`` so the TestingDatabaseModule's first-resolution
        ``reset_for_tests()`` (which disposes any prior engine) fires
        first. Otherwise ``create_all`` would write to an engine that's
        about to be wiped, and the next request would lazy-init a fresh
        empty one.
        """
        from agentclaw.community.core.base import Base

        # Side-effect imports — register each ORM class on ``Base.metadata``
        # (the class statement runs, the declarative metaclass attaches a
        # ``Table`` to the shared ``MetaData``). ``noqa: F401`` because the
        # name itself is intentionally unused — only the import side effect
        # matters.
        import agentclaw.community.plugin_api.models  # noqa: F401  ac_bots / ac_resource / ac_channel_config
        import agentclaw.community.core.models  # noqa: F401  ac_skill* / ac_skill_set_mcp / ac_user_mcp_config / propagation_log / center_sync_log
        import agentclaw.community.core.skill_center.local_skill_cleanup  # noqa: F401  obsolete Local Skill package cleanup work
        import agentclaw.community.core.skill_center.orm  # noqa: F401  ac_default_skillset_*
        import agentclaw.community.core.access.sqlite_models  # noqa: F401  ac_access_control_policy / ac_user_info
        import agentclaw.community.core.service_bot.repository.models  # noqa: F401  ac_bot_publish
        import agentclaw.community.core.bot_public.repository.models  # noqa: F401  ac_bot_friend
        import agentclaw.community.core.expert_chat.sqlite_models  # noqa: F401  ac_expert_chat_bot_sessions
        import agentclaw.community.core.devices.repository.models  # noqa: F401  ac_entity_device_binding
        import agentclaw.community.core.bot_management.repository.models  # noqa: F401  ac_templates / ac_bot_restart_lock
        import agentclaw.community.core.bot_management.render_screen.sqlite_models  # noqa: F401  ac_bot_render_screen
        import agentclaw.community.core.system_config.orm  # noqa: F401  ac_config_*
        import agentclaw.community.core.harness.sqlite_models  # noqa: F401  ac_harness_*
        import agentclaw.community.core.bot_chat.models  # noqa: F401  bot_chat private-Base tables
        import agentclaw.community.core.bot_dormant.sqlite_models  # noqa: F401  ac_bot_dormant_*
        import agentclaw.community.core.task_queue.repository.models  # noqa: F401  ac_task_queue
        import agentclaw.community.core.skills_pool.repository.models  # noqa: F401  ac_bot_skill_layout_state
        import agentclaw.community.core.session_resources.repository.models  # noqa: F401  ac_session_resource
        import agentclaw.community.core.economy.governance.orm  # noqa: F401  governance_*
        import agentclaw.community.core.caller_identity.models  # noqa: F401  caller identity tables
        import agentclaw.community.core.bot_app_grant.models  # noqa: F401  ac_bot_app_grant / ac_bot_app_grant_log
        import agentclaw.community.core.user_list.models  # noqa: F401  ac_entity_user_list

        # bot_chat uses a private ``Base = declarative_base()`` instead of
        # the canonical ``agentclaw.community.core.base.Base``. Side-effect import
        # registers ``AwLangfuseTrace`` + ``AcBot`` on the private metadata;
        # the canonical ``create_all`` below won't see them, so we call
        # ``create_all`` on the private Base too. ``ac_bots`` is also
        # defined on the canonical Base (BotModel) — SQLAlchemy permits the
        # same table name across different MetaData objects, and
        # ``create_all`` is idempotent (``checkfirst=True`` skips already-
        # existing tables), so the order of the two calls doesn't matter
        # for correctness; we still register canonical ``ac_bots`` first
        # for clarity. Without this block, ``/api/v1/bot-chats`` list/get
        # crashes at runtime with ``sqlite3.OperationalError: no such
        # table: aw_langfuse_traces``.
        # Any future ORM class added to core/bot_chat/models.py is picked up
        # automatically via the same create_all — no further bootstrap edits needed.
        import agentclaw.community.core.bot_chat.models as _bot_chat_models  # noqa: F401  bot_chat private Base

        _get_session_factory()
        Base.metadata.create_all(_engine)
        _bot_chat_models.Base.metadata.create_all(_engine)

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
