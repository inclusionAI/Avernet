from agentcompute.community.bootstrap import get_container
from agentcompute.community.plugins import register_plugins
from agentcompute.community.plugins.database import SqliteDatabasePlugin


def test_sqlite_connect_creates_schema(tmp_path):
    db = SqliteDatabasePlugin(f"sqlite:///{tmp_path / 'test.db'}")
    db.connect()
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {r["name"] for r in rows}
    assert {"requests", "runs", "plans", "node_executions"} <= tables


def test_sqlite_session_commits_and_rolls_back(tmp_path):
    db = SqliteDatabasePlugin(f"sqlite:///{tmp_path / 'test.db'}")
    db.connect()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO requests (id, goal, agents, provider, payload) "
            "VALUES ('req1', 'g', '[]', 'stub', '{}')"
        )
        conn.execute(
            "INSERT INTO runs (id, request_id, goal, agents, provider, status) "
            "VALUES ('r1', 'req1', 'g', '[]', 'stub', 'running')"
        )
    runs = db.execute("SELECT goal FROM runs").fetchall()
    assert runs[0]["goal"] == "g"


def test_in_memory_database(tmp_path):
    db = SqliteDatabasePlugin("sqlite:///:memory:")
    db.connect()
    assert db.execute("SELECT 1").fetchone() is not None


def test_database_resolves_via_container():
    register_plugins()
    db = get_container().plugins().database()
    assert isinstance(db, SqliteDatabasePlugin)
    db.close()
