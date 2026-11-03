"""Environment-scoped build manifests and DB-time fenced execution ownership."""

from contextlib import contextmanager

from search_core.api.index_builds import IndexBuildConflict, IndexBuildRecord
from search_core.config.index_builds import IndexBuildConfig
from search_core.providers.sql_index_documents import validate_environment

TABLE = "bcsfuse_index_builds"
SQLITE_SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    environment TEXT NOT NULL, build_id TEXT NOT NULL, config_json TEXT NOT NULL,
    status TEXT NOT NULL, completed_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
    owner_token TEXT NOT NULL DEFAULT '', lease_until REAL,
    gmt_create REAL NOT NULL, gmt_modify REAL NOT NULL,
    PRIMARY KEY(environment, build_id))"""
COLUMNS = "config_json,status,completed_count,total_count,error_code"


class SqlIndexBuildRepository:
    def __init__(self, connect, *, environment, placeholder="%s"):
        if placeholder not in {"?", "%s"}:
            raise ValueError("invalid SQL placeholder")
        self.connect, self.environment = connect, validate_environment(environment)
        self.placeholder = placeholder
        self.now = (
            "(julianday('now')-2440587.5)*86400.0"
            if placeholder == "?"
            else "CURRENT_TIMESTAMP(6)"
        )
        self.deadline = (
            f"{self.now}+?"
            if placeholder == "?"
            else f"TIMESTAMPADD(MICROSECOND,?,{self.now})"
        )

    @contextmanager
    def cursor(self):
        conn = self.connect()
        try:
            cur = conn.cursor()
            try:
                yield conn, cur
            finally:
                cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, cur, sql, args=()):
        return cur.execute(sql.replace("?", self.placeholder), args)

    def duration(self, seconds):
        return seconds if self.placeholder == "?" else round(seconds * 1_000_000)

    @staticmethod
    def decode(row):
        if row is None:
            return None
        if isinstance(row, dict):
            row = tuple(row[k] for k in COLUMNS.split(","))
        return IndexBuildRecord(
            IndexBuildConfig.model_validate_json(row[0]),
            row[1],
            int(row[2]),
            int(row[3]),
            row[4],
        )

    def get(self, build_id):
        with self.cursor() as (_, cur):
            self.execute(
                cur,
                f"SELECT {COLUMNS} FROM {TABLE} WHERE environment=? AND build_id=?",
                (self.environment, build_id),
            )
            return self.decode(cur.fetchone())

    def create(self, config, *, enqueue=True):
        current = self.get(config.build_id)
        if current is None:
            try:
                with self.cursor() as (conn, cur):
                    self.execute(
                        cur,
                        f"INSERT INTO {TABLE} (environment,build_id,config_json,status,gmt_create,gmt_modify) VALUES (?,?,?,?,{self.now},{self.now})",
                        (
                            self.environment,
                            config.build_id,
                            config.model_dump_json(),
                            "pending" if enqueue else "configured",
                        ),
                    )
                    conn.commit()
            except Exception:
                current = self.get(config.build_id)
                if current is None:
                    raise
            else:
                current = self.get(config.build_id)
        if current.config != config:
            raise IndexBuildConflict("build ID already has different configuration")
        if enqueue and current.status in {"configured", "failed"}:
            with self.cursor() as (conn, cur):
                self.execute(
                    cur,
                    f"UPDATE {TABLE} SET status='pending',error_code='',owner_token='',lease_until=NULL,gmt_modify={self.now} WHERE environment=? AND build_id=? AND status IN ('configured','failed')",
                    (self.environment, config.build_id),
                )
                conn.commit()
        return self.get(config.build_id)

    def candidates(self):
        with self.cursor() as (_, cur):
            self.execute(
                cur,
                f"SELECT {COLUMNS} FROM {TABLE} WHERE environment=? AND (status='pending' OR (status='running' AND lease_until<{self.now})) ORDER BY gmt_create,build_id LIMIT 20",
                (self.environment,),
            )
            return [self.decode(row) for row in cur.fetchall()]

    def claim(self, build_id, token, seconds):
        with self.cursor() as (conn, cur):
            self.execute(
                cur,
                f"UPDATE {TABLE} SET status='running',owner_token=?,lease_until={self.deadline},error_code='',gmt_modify={self.now} WHERE environment=? AND build_id=? AND (status='pending' OR (status='running' AND lease_until<{self.now}))",
                (token, self.duration(seconds), self.environment, build_id),
            )
            won = cur.rowcount == 1
            conn.commit()
            return won

    def progress(self, build_id, token, done, total, seconds):
        with self.cursor() as (conn, cur):
            self.execute(
                cur,
                f"UPDATE {TABLE} SET completed_count=?,total_count=?,lease_until={self.deadline},gmt_modify={self.now} WHERE environment=? AND build_id=? AND status='running' AND owner_token=? AND lease_until>{self.now}",
                (
                    done,
                    total,
                    self.duration(seconds),
                    self.environment,
                    build_id,
                    token,
                ),
            )
            updated = cur.rowcount == 1
            conn.commit()
            return updated

    def finish(self, build_id, token, status, done, total, error):
        if status not in {"pending", "completed", "failed"}:
            raise ValueError("invalid build completion status")
        with self.cursor() as (conn, cur):
            self.execute(
                cur,
                f"UPDATE {TABLE} SET status=?,completed_count=?,total_count=?,error_code=?,owner_token='',lease_until=NULL,gmt_modify={self.now} WHERE environment=? AND build_id=? AND status='running' AND owner_token=? AND lease_until>{self.now}",
                (status, done, total, error, self.environment, build_id, token),
            )
            updated = cur.rowcount == 1
            conn.commit()
            return updated
