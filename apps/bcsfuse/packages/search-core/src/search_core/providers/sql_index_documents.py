"""DB-API durable retrieval artifacts using TC's vector/payload/version layout.

New schemas add environment and index_key. Schema provisioning is explicit;
SQLite initialization is local-only. No framework, Card or Worker dependency.
"""

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal

from search_core.plugin_api.index_documents import (
    DocumentChanges,
    DocumentVersion,
    IndexDocument,
)
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.card_schema import validate_card_table

TABLE = "search_index_documents"
COLUMNS = "id, vector, payload, source_revision, extra, is_deleted, version, gmt_modify, filters"
VERSION_COLUMNS = "id, source_id, version, is_deleted, gmt_modify"
SQLITE_SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    environment TEXT NOT NULL, index_key TEXT NOT NULL, id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    vector TEXT NOT NULL, payload TEXT NOT NULL, extra TEXT NOT NULL, filters TEXT,
    source_revision INTEGER NOT NULL, version INTEGER NOT NULL,
    is_deleted INTEGER NOT NULL, gmt_modify REAL NOT NULL,
    PRIMARY KEY(environment, index_key, id))"""


def validate_environment(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
        raise ValueError("explicit environment required")
    return value


class SqlIndexDocuments:
    def __init__(
        self,
        connect,
        *,
        environment: str,
        index_key: str,
        placeholder="%s",
        table=TABLE,
    ):
        self.table = validate_card_table(table)
        self.environment = validate_environment(environment)
        if not isinstance(index_key, str) or not index_key or len(index_key) > 64:
            raise ValueError("index_key of 1-64 characters required")
        if placeholder not in {"?", "%s"}:
            raise ValueError("invalid placeholder")
        self.connect, self.index_key, self.placeholder = connect, index_key, placeholder
        self.now_expression = (
            "(julianday('now') - 2440587.5) * 86400.0"
            if placeholder == "?"
            else "UNIX_TIMESTAMP(CURRENT_TIMESTAMP(6))"
        )

    @contextmanager
    def _cursor(self):
        conn = self.connect()
        try:
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()
        finally:
            conn.close()

    def _execute(self, cur, sql, params=()):
        return cur.execute(sql.replace("?", self.placeholder), params)

    @property
    def columns(self):
        return self._read_columns(COLUMNS)

    def _read_columns(self, columns):
        if self.placeholder == "?":
            return columns
        return columns.replace("gmt_modify", "UNIX_TIMESTAMP(gmt_modify) AS gmt_modify")

    @property
    def _time_parameter(self):
        return "?" if self.placeholder == "?" else "FROM_UNIXTIME(?)"

    @property
    def scope(self):
        return self.environment, self.index_key

    @staticmethod
    def decode(row):
        if row is None:
            return None
        if isinstance(row, dict):
            row = tuple(row[k] for k in COLUMNS.split(", "))
        return IndexDocument(
            VectorPoint(
                id=row[0], vector=json.loads(row[1]), payload=json.loads(row[2])
            ),
            int(row[3]),
            json.loads(row[4]),
            bool(row[5]),
            int(row[6]),
            float(row[7]),
            None
            if row[8] is None
            else {k: tuple(v) for k, v in json.loads(row[8]).items()},
        )

    def load_by_id(self, id):
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND index_key=? AND id=?",
                (*self.scope, id),
            )
            return self.decode(cur.fetchone())

    def load_many(self, ids):
        """Read one bounded document group; caller validates its source revision."""
        ids = tuple(dict.fromkeys(ids))
        if not ids:
            return {}
        if len(ids) > 256:
            raise ValueError("at most 256 document IDs per read")
        with self._cursor() as cur:
            marks = ",".join("?" for _ in ids)
            self._execute(
                cur,
                f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND index_key=? AND id IN ({marks})",
                (*self.scope, *ids),
            )
            documents = [self.decode(row) for row in cur.fetchall()]
        return {d.id: d for d in documents}

    def load_by_source(self, source_id):
        """Read a source group in one statement, including retained tombstones."""
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND index_key=? AND source_id=?",
                (*self.scope, source_id),
            )
            return [self.decode(row) for row in cur.fetchall()]

    @staticmethod
    def decode_version(row):
        if isinstance(row, dict):
            row = tuple(row[k] for k in VERSION_COLUMNS.split(", "))
        return DocumentVersion(row[0], row[1], int(row[2]), bool(row[3]), float(row[4]))

    def iter_version_batches(self, batch_size):
        """Version reconciliation and startup discovery, without vector transfer."""
        if batch_size < 1:
            raise ValueError("positive batch size required")
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT MAX(id) FROM {self.table} WHERE environment=? AND index_key=?",
                self.scope,
            )
            upper = cur.fetchone()[0]
        if upper is None:
            return
        after = ""
        while True:
            with self._cursor() as cur:
                self._execute(
                    cur,
                    f"SELECT {self._read_columns(VERSION_COLUMNS)} FROM {self.table} WHERE environment=? AND index_key=? AND id>? AND id<=? ORDER BY id LIMIT ?",
                    (*self.scope, after, upper, batch_size),
                )
                rows = cur.fetchall()
            if not rows:
                return
            records = [self.decode_version(row) for row in rows]
            yield records
            after = records[-1].id
            if len(records) < batch_size:
                return

    def load_latest_by_id(self, id):
        """Cross-generation cache lookup, still isolated by environment.

        A caller must check the cached model key before reusing vectors. This
        method is not used for index recovery or search.
        """
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND id=? ORDER BY gmt_modify DESC LIMIT 1",
                (self.environment, id),
            )
            return self.decode(cur.fetchone())

    def load_vector_candidates(self, source_id, embedding_key):
        """Reuse candidates, not a snapshot: no source revision or index selection."""
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND source_id=? AND is_deleted=0 ORDER BY gmt_modify DESC, index_key",
                (self.environment, source_id),
            )
            candidates = []
            for row in cur.fetchall():
                document = self.decode(row)
                if document.metadata.get(
                    "embedding_key"
                ) == embedding_key and document.metadata.get("text_hash"):
                    candidates.append(document)
            return candidates

    def save_in_transaction(self, cur, document):
        """Caller owns the transaction and source-version/lease fence.

        This makes publishing the artifact and releasing preparation ownership
        atomic with the source row. An older source revision never replaces a new one.
        """
        self._execute(
            cur,
            f"SELECT {self._read_columns('source_revision, version, gmt_modify')} FROM {self.table} WHERE environment=? AND index_key=? AND id=?",
            (*self.scope, document.id),
        )
        current = cur.fetchone()
        if current is not None and isinstance(current, dict):
            current = tuple(
                current[k] for k in ("source_revision", "version", "gmt_modify")
            )
        if current is not None and current[0] > document.source_revision:
            return False
        self._execute(cur, f"SELECT {self.now_expression}")
        raw_now = cur.fetchone()[0]
        if self.placeholder == "?":
            now = float(raw_now)
            modified = max(now, float(current[2]) + 0.000001) if current else now
        else:
            now = Decimal(str(raw_now))
            modified = (
                max(now, Decimal(str(current[2])) + Decimal("0.000001"))
                if current
                else now
            )
        values = (
            document.source_id,
            json.dumps(document.point.vector, allow_nan=False),
            json.dumps(document.point.payload, ensure_ascii=False, allow_nan=False),
            json.dumps(document.metadata, ensure_ascii=False, allow_nan=False),
            None
            if document.filters is None
            else json.dumps(document.filters, ensure_ascii=False, allow_nan=False),
            document.source_revision,
            (int(current[1]) + 1) if current else 1,
            int(document.is_deleted),
            modified,
        )
        if current is None:
            self._execute(
                cur,
                f"INSERT INTO {self.table} (source_id,vector,payload,extra,filters,source_revision,version,is_deleted,gmt_modify,environment,index_key,id) VALUES (?,?,?,?,?,?,?,?,{self._time_parameter},?,?,?)",
                (*values, *self.scope, document.id),
            )
        else:
            self._execute(
                cur,
                f"UPDATE {self.table} SET source_id=?,vector=?,payload=?,extra=?,filters=?,source_revision=?,version=?,is_deleted=?,gmt_modify={self._time_parameter} WHERE environment=? AND index_key=? AND id=?",
                (*values, *self.scope, document.id),
            )
        return True

    def get_last_modified_time(self):
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT {('MAX(gmt_modify)' if self.placeholder == '?' else 'UNIX_TIMESTAMP(MAX(gmt_modify))')} FROM {self.table} WHERE environment=? AND index_key=?",
                self.scope,
            )
            row = cur.fetchone()
            return float(row[0] or 0)

    def iter_batches(self, batch_size):
        if batch_size < 1:
            raise ValueError("positive batch size required")
        with self._cursor() as cur:
            self._execute(
                cur,
                f"SELECT MAX(id) FROM {self.table} WHERE environment=? AND index_key=?",
                self.scope,
            )
            upper = cur.fetchone()[0]
        if upper is None:
            return
        after = ""
        while True:
            with self._cursor() as cur:
                self._execute(
                    cur,
                    f"SELECT {self.columns} FROM {self.table} WHERE environment=? AND index_key=? AND id>? AND id<=? ORDER BY id LIMIT ?",
                    (*self.scope, after, upper, batch_size),
                )
                rows = cur.fetchall()
            if not rows:
                return
            records = [self.decode(row) for row in rows]
            yield records
            after = records[-1].id
            if len(records) < batch_size:
                return

    def iter_versions(self, batch_size):
        """Cheap reconciliation: timestamps alone cannot order transaction commits."""
        if batch_size < 1:
            raise ValueError("positive batch size required")
        after = ""
        while True:
            with self._cursor() as cur:
                self._execute(
                    cur,
                    f"SELECT id,version FROM {self.table} WHERE environment=? AND index_key=? AND id>? ORDER BY id LIMIT ?",
                    (*self.scope, after, batch_size),
                )
                rows = cur.fetchall()
            if not rows:
                return
            versions = [
                (row["id"], row["version"]) if isinstance(row, dict) else tuple(row)
                for row in rows
            ]
            yield versions
            after = versions[-1][0]
            if len(versions) < batch_size:
                return

    def iter_change_batches_since(self, checkpoint, batch_size):
        for records in self._iter_changes(checkpoint, batch_size, lightweight=False):
            yield DocumentChanges(records, records[-1].modified_at)

    def iter_change_versions_since(self, checkpoint, batch_size):
        yield from self._iter_changes(checkpoint, batch_size, lightweight=True)

    def _iter_changes(self, checkpoint, batch_size, *, lightweight):
        if batch_size < 1:
            raise ValueError("positive batch size required")
        columns = self._read_columns(VERSION_COLUMNS if lightweight else COLUMNS)
        decode = self.decode_version if lightweight else self.decode
        upper = self.get_last_modified_time()
        if not upper:
            return
        # FROM_UNIXTIME(-1) is NULL in MySQL; epoch zero is the lower bound.
        after_time, after_id = (0.0 if self.placeholder == "%s" else -1.0), ""
        stamp = self._time_parameter
        while True:
            with self._cursor() as cur:
                self._execute(
                    cur,
                    f"""SELECT {columns} FROM {self.table}
                    WHERE environment=? AND index_key=? AND gmt_modify>={stamp} AND gmt_modify<={stamp}
                    AND (gmt_modify>{stamp} OR (gmt_modify={stamp} AND id>?))
                    ORDER BY gmt_modify,id LIMIT ?""",
                    (
                        *self.scope,
                        checkpoint,
                        upper,
                        after_time,
                        after_time,
                        after_id,
                        batch_size,
                    ),
                )
                rows = cur.fetchall()
            if not rows:
                return
            records = [decode(row) for row in rows]
            # Inclusive checkpoint on the next poll replays equal-timestamp writes.
            yield records
            after_time, after_id = records[-1].modified_at, records[-1].id
            if len(records) < batch_size:
                return


def initialize_sqlite_documents(conn, *, table=TABLE):
    table = validate_card_table(table)
    conn.execute(SQLITE_SCHEMA.replace(TABLE, table, 1))
    index_prefix = "idx_search_documents" if table == TABLE else f"idx_{table}"
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if "filters" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN filters TEXT")
    if "source_id" not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN source_id TEXT NOT NULL DEFAULT ''"
        )
        cursor = conn.execute(f"SELECT environment,index_key,id,payload FROM {table}")
        while rows := cursor.fetchmany(100):
            conn.executemany(
                f"UPDATE {table} SET source_id=? WHERE environment=? AND index_key=? AND id=?",
                [
                    (json.loads(row[3]).get("source_id", row[2]), *row[:3])
                    for row in rows
                ],
            )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {index_prefix}_sync ON {table}(environment,index_key,gmt_modify,id)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {index_prefix}_source ON {table}(environment,index_key,source_id)"
    )


def sqlite_index_documents(path, *, environment, index_key, table=TABLE):
    store = SqlIndexDocuments(
        lambda: sqlite3.connect(path, timeout=10),
        environment=environment,
        index_key=index_key,
        placeholder="?",
        table=table,
    )
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        initialize_sqlite_documents(conn, table=table)
    store.path = path
    return store
