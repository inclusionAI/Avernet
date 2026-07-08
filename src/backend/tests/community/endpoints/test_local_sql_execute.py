"""Coverage for ``POST /local/sql/execute`` (local-mode SQL endpoint).

Happy path: INSERT a row via the endpoint, then read it back through
the same ``DatabasePlugin`` to prove the write committed against the
per-test in-memory SQLite engine.

Error path: send a disallowed verb (``PRAGMA``) and expect the endpoint
to reject it with 400 before touching the DB. Covers the verb-allowlist
guard in ``api/local/sql.py``.
"""
from __future__ import annotations

from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_INSERT_SQL = (
    "INSERT INTO ac_entity_device_binding ("
    "entity_id, entity_type, device_id, device_provider, env, device_props, "
    "status, applied_by, gmt_create, gmt_modified"
    ") VALUES ("
    ":eid, :etype, :did, :dprov, :env, :props, :st, :by, "
    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)

_INSERT_PARAMS = {
    "eid": "u_local_sql_test",
    "etype": "staff",
    "did": "dev_local_sql_test",
    "dprov": "local",
    "env": "dev",
    "props": "{}",
    "st": "ACTIVE",
    "by": "u_local_sql_test",
}


def _row_committed(response, world) -> None:
    """The endpoint write must be visible to a fresh session on the
    same DatabasePlugin (the per-test in-memory engine).
    """
    db = world.get(DatabasePlugin)
    with db.session() as session:
        rows = session.execute(
            text(
                "SELECT entity_id, device_id, status FROM ac_entity_device_binding "
                "WHERE entity_id = :eid"
            ),
            {"eid": "u_local_sql_test"},
        ).fetchall()
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    eid, did, st = rows[0]
    assert eid == "u_local_sql_test"
    assert did == "dev_local_sql_test"
    assert st == "ACTIVE"


@endpoint_test(
    method="POST",
    path="/local/sql/execute",
    scenario="insert_ok",
    input=CaseInput(json_body={"sql": _INSERT_SQL, "params": _INSERT_PARAMS}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"results": [{"rowcount": 1}]},
    ),
    extra_assertions=(_row_committed,),
)
def local_sql_execute_insert_ok():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="POST",
    path="/local/sql/execute",
    scenario="disallowed_verb",
    input=CaseInput(json_body={"sql": "PRAGMA foreign_keys=OFF"}),
    expect=ExpectError(
        status=400,
        json_contains={"detail": "Only ['DELETE', 'INSERT', 'SELECT', 'UPDATE', 'WITH'] statements are allowed (got 'PRAGMA')"},
    ),
)
def local_sql_execute_disallowed_verb():
    """Declarative case — body intentionally empty."""
