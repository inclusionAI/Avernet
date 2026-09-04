"""SQLite implementation of the database SPI (stdlib ``sqlite3``)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from ...spi._database import DatabasePlugin

__all__ = ["SqliteDatabasePlugin"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    agents TEXT NOT NULL,
    provider TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    agents TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id)
);

CREATE TABLE IF NOT EXISTS plans (
    run_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS node_executions (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, node_id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""


class SqliteDatabasePlugin(DatabasePlugin):
    """SQLite-backed ``DatabasePlugin`` with a single shared, lock-guarded connection.

    ``database_url`` follows ``sqlite:///<path>`` (or ``:memory:``). Schema is
    created idempotently on ``connect``.
    """

    def __init__(self, database_url: str = "sqlite:///agentcompute.db") -> None:
        self._path = self._extract_path(database_url)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        connection = self._get_connection()
        connection.executescript(_SCHEMA)
        connection.commit()

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection]:
        with self._lock:
            connection = self._get_connection()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with self._lock:
            connection = self._get_connection()
            cursor = connection.execute(sql, params)
            connection.commit()
            return cursor

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _get_connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(self._path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
            return self._connection

    @staticmethod
    def _extract_path(database_url: str) -> str:
        if database_url == "sqlite:///:memory:" or database_url == ":memory:":
            return ":memory:"
        if database_url.startswith("sqlite:///"):
            return database_url[len("sqlite:///") :]
        return database_url
