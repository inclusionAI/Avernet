# tests/plugins/local/test_sqlite_db.py
"""Tests for SqliteDB — local-mode DatabasePlugin implementation."""


class TestSqliteDBSession:
    """SqliteDB.session() yields a usable SQLAlchemy Session."""

    def test_session_yields_and_closes(self):
        """session() context manager yields a Session that can execute SQL."""
        from agentclaw.community.plugins.local.database import SqliteDB

        db = SqliteDB()
        with db.session() as session:
            # Should be able to execute raw SQL on the session
            result = session.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
            assert result.scalar() == 1

    def test_each_session_is_independent(self):
        """Each session() call yields a fresh, independent Session."""
        from agentclaw.community.plugins.local.database import SqliteDB

        db = SqliteDB()
        with db.session() as s1:
            pass
        with db.session() as s2:
            # Each call should give a distinct session object
            assert s1 is not s2

    def test_no_compat_import(self):
        """SqliteDB must NOT import from compat."""
        import inspect
        from agentclaw.community.plugins.local import database as mod

        source = inspect.getsource(mod)
        assert "skill_center.compat" not in source
        assert "services.openclawserver" not in source
