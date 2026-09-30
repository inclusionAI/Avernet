"""Local-mode SQL execution endpoint.

Mounted only when ``runtime.is_local`` is true (wired via
``OptionalRouters`` bound in ``TestingInfrastructureModule``). The local
SQLite DB is in-memory and wiped on every backend restart, so seeding
via HTTP after boot is the simplest dev workflow.

Restricted to DML (INSERT/UPDATE/DELETE/SELECT/WITH). DDL, PRAGMA,
ATTACH, and multi-statement scripts are rejected — even in local mode
the endpoint is unauthenticated and worth keeping narrow.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.database import DatabasePlugin


router = APIRouter(prefix="/local/sql", tags=["local-sql"])


_ALLOWED_VERBS = {"INSERT", "UPDATE", "DELETE", "SELECT", "WITH"}
_LEADING_COMMENT_RE = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/)", re.DOTALL)


def _strip_leading_comments(sql: str) -> str:
    prev = None
    while prev != sql:
        prev = sql
        sql = _LEADING_COMMENT_RE.sub("", sql, count=1)
    return sql.lstrip()


def _validate(sql: str) -> str:
    stripped = _strip_leading_comments(sql).rstrip().rstrip(";").rstrip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Empty SQL")
    if ";" in stripped:
        raise HTTPException(status_code=400, detail="Multi-statement SQL not allowed")
    verb = stripped.split(None, 1)[0].upper()
    if verb not in _ALLOWED_VERBS:
        raise HTTPException(
            status_code=400,
            detail=f"Only {sorted(_ALLOWED_VERBS)} statements are allowed (got {verb!r})",
        )
    return stripped


class SqlStatement(BaseModel):
    sql: str = Field(..., description="Single SQL statement (no trailing ';')")
    params: dict[str, Any] | None = Field(
        default=None,
        description="Named bind parameters, e.g. {'name': 'foo'} for :name",
    )


class ExecuteRequest(BaseModel):
    sql: str | None = None
    params: dict[str, Any] | None = None
    statements: list[SqlStatement] | None = None


class StatementResult(BaseModel):
    rowcount: int
    lastrowid: int | None = None
    rows: list[dict[str, Any]] | None = None


class ExecuteResponse(BaseModel):
    results: list[StatementResult]


def _run_one(session, stmt: SqlStatement) -> StatementResult:
    sql = _validate(stmt.sql)
    result = session.execute(text(sql), stmt.params or {})
    rows = None
    if result.returns_rows:
        rows = [dict(r._mapping) for r in result.fetchall()]
    lastrowid = getattr(result, "lastrowid", None)
    if not result.returns_rows and lastrowid == 0:
        lastrowid = None
    return StatementResult(
        rowcount=result.rowcount if result.rowcount is not None else -1,
        lastrowid=lastrowid,
        rows=rows,
    )


@router.post("/execute", response_model=ExecuteResponse)
def execute_sql(
    req: ExecuteRequest,
    db: DatabasePlugin = Injected(DatabasePlugin),
) -> ExecuteResponse:
    if req.statements is None and req.sql is None:
        raise HTTPException(status_code=400, detail="Provide 'sql' or 'statements'")
    if req.statements is not None and req.sql is not None:
        raise HTTPException(status_code=400, detail="Provide only one of 'sql' or 'statements'")

    statements = req.statements or [SqlStatement(sql=req.sql or "", params=req.params)]

    results: list[StatementResult] = []
    try:
        with db.orm_session() as session:
            for stmt in statements:
                results.append(_run_one(session, stmt))
    except SQLAlchemyError as exc:
        # Surface DB-level failures (constraint violations, syntax errors,
        # missing tables) as a 400 with the underlying message instead of
        # a generic 500 — far more useful for the dev driving this endpoint.
        msg = str(getattr(exc, "orig", exc)) or exc.__class__.__name__
        raise HTTPException(status_code=400, detail=f"SQL error: {msg}") from exc
    return ExecuteResponse(results=results)
