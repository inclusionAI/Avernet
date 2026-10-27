"""
SQLite Schema for Worker Registry

Stage 1 数据库表结构定义。设计目标：
1. 支持 PostgreSQL 平滑迁移
2. 支持 version 乐观锁
3. 支持一个 worker 只有一个 active profile

表结构：
- workers: Worker 主表
- worker_runtime_states: 运行态表（独立以便高频更新）
- worker_profile_bindings: Profile 绑定关系表
- worker_audit_logs: 审计日志表
"""

from __future__ import annotations

import sqlite3
from typing import Optional


# ============================================================================
# Schema Definition SQL
# ============================================================================

SCHEMA_VERSION = 1

CREATE_WORKERS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_workers (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    identity_name TEXT NOT NULL,
    identity_handle TEXT NOT NULL,
    identity_description TEXT,
    responsibilities TEXT NOT NULL,  -- JSON array
    capabilities TEXT NOT NULL,  -- JSON array
    skills TEXT NOT NULL,  -- JSON array
    resources TEXT NOT NULL,  -- JSON array
    state_availability TEXT NOT NULL,
    state_trust_level TEXT NOT NULL,
    state_runtime_state TEXT NOT NULL DEFAULT 'offline',
    domains TEXT NOT NULL,  -- JSON array
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    source_type TEXT NOT NULL DEFAULT 'api',
    source_ref TEXT,
    external_id TEXT,
    active_profile_key TEXT,
    config TEXT,  -- JSON object, Worker 行为配置
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    updated_by TEXT,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bcsfuse_workers_lifecycle_state ON bcsfuse_workers(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_bcsfuse_workers_source_type ON bcsfuse_workers(source_type);
CREATE INDEX IF NOT EXISTS idx_bcsfuse_workers_external_id ON bcsfuse_workers(external_id);
"""

CREATE_WORKER_RUNTIME_STATES_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_worker_runtime_states (
    worker_id TEXT PRIMARY KEY,
    runtime_state TEXT NOT NULL DEFAULT 'offline',
    updated_by TEXT,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_runtime_states_state ON bcsfuse_worker_runtime_states(runtime_state);
"""

CREATE_WORKER_PROFILE_BINDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_worker_profile_bindings (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    bound_at TEXT,
    unbound_at TEXT,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL,
    UNIQUE(worker_id, profile_key)
);

-- 确保每个 worker 只有一个 active profile
CREATE UNIQUE INDEX IF NOT EXISTS idx_bcsfuse_worker_profile_bindings_active
    ON bcsfuse_worker_profile_bindings(worker_id)
    WHERE is_active = 1;
"""

CREATE_WORKER_AUDIT_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_worker_audit_logs (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source_type TEXT NOT NULL DEFAULT 'api',
    source_ref TEXT,
    performed_by TEXT,
    performed_at TEXT NOT NULL,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_audit_logs_worker_id ON bcsfuse_worker_audit_logs(worker_id);
CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_audit_logs_action ON bcsfuse_worker_audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_audit_logs_performed_at ON bcsfuse_worker_audit_logs(performed_at);
"""


# ============================================================================
# Schema Initialization
# ============================================================================

def init_schema(conn: sqlite3.Connection) -> None:
    """
    初始化数据库 Schema

    Args:
        conn: SQLite 连接
    """
    cursor = conn.cursor()

    # 创建表
    cursor.executescript(CREATE_WORKERS_TABLE)
    cursor.executescript(CREATE_WORKER_RUNTIME_STATES_TABLE)
    cursor.executescript(CREATE_WORKER_PROFILE_BINDINGS_TABLE)
    cursor.executescript(CREATE_WORKER_AUDIT_LOGS_TABLE)

    # 创建 schema_version 表用于跟踪版本
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
    """)

    # 设置 schema version
    cursor.execute(
        "INSERT OR REPLACE INTO schema_version(version) VALUES (?)",
        (SCHEMA_VERSION,)
    )

    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """
    获取当前 Schema 版本

    Args:
        conn: SQLite 连接

    Returns:
        Schema 版本号，如果不存在返回 None
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT version FROM schema_version LIMIT 1"
    )
    row = cursor.fetchone()
    return row[0] if row else None


__all__ = [
    "SCHEMA_VERSION",
    "CREATE_WORKERS_TABLE",
    "CREATE_WORKER_RUNTIME_STATES_TABLE",
    "CREATE_WORKER_PROFILE_BINDINGS_TABLE",
    "CREATE_WORKER_AUDIT_LOGS_TABLE",
    "init_schema",
    "get_schema_version",
]