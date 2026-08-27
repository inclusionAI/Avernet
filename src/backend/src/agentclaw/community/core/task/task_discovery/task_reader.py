"""TaskReader — 读取已发现的任务数据。

``OrmTaskReader`` 通过 ``DatabasePlugin.orm_session()`` 读取（推荐），
corp 走 OceanBase、local 走 SQLite 内存库。
``SqliteTaskReader`` 从本地 SQLite db 文件读取（向后兼容，仅测试用）；
``MockTaskReader`` 从本地 JSON 文件读取(向后兼容)。
未来可替换为真实数据源(消息管线、行为分析平台等)，
只需实现同一 ``TaskReader`` Protocol。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from injector import inject

from agentclaw.community.core.task.task_discovery.discovered_task_models import (
    DiscoveredTaskModel,
)
from agentclaw.community.core.task.task_discovery.models import DiscoveredTask
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()

#: 建表 DDL
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS discovered_tasks (
    task_id            TEXT PRIMARY KEY,
    bot_id             TEXT NOT NULL,
    owner_id           TEXT NOT NULL,
    dt                 TEXT NOT NULL,
    title              TEXT NOT NULL,
    instruction        TEXT,
    background         TEXT,
    discovery_basis    TEXT,
    priority           TEXT DEFAULT 'medium',
    discovered_at      TEXT,
    status             TEXT DEFAULT 'pending_confirmation',
    objective          TEXT,
    acceptances        TEXT
);
CREATE INDEX IF NOT EXISTS idx_discovered_tasks_bot_owner_dt
    ON discovered_tasks(bot_id, owner_id, dt);
"""

#: 查询全量任务的 SQL（SELECT *：容纳可能新增的列，_row_to_task 防御性读）
_SELECT_ALL_SQL = "SELECT * FROM discovered_tasks;"

#: 按 (bot_id, owner_id, dt) 查询待确认任务的 SQL
_SELECT_PENDING_FOR_BOT_SQL = (
    "SELECT * FROM discovered_tasks "
    "WHERE bot_id = ? AND owner_id = ? AND dt = ? "
    "AND status = 'pending_confirmation';"
)


def _row_to_task(row: sqlite3.Row) -> DiscoveredTask:
    """将 sqlite3.Row 映射为 DiscoveredTask。

    ``objective`` / ``acceptances`` 为后加的列，对旧库（缺这两列）防御性读取：
    无则以缺省值/空回退，与 DiscoveredTask 字段默认一致。
    """
    keys = set(row.keys())
    raw_acceptances = row["acceptances"] if "acceptances" in keys else None
    try:
        acceptances = json.loads(raw_acceptances) if raw_acceptances else []
    except (TypeError, ValueError):
        acceptances = []
    return DiscoveredTask(
        task_id=row["task_id"],
        bot_id=row["bot_id"],
        owner_id=row["owner_id"],
        dt=row["dt"],
        title=row["title"],
        instruction=row["instruction"] or "",
        background=row["background"] or "",
        discovery_basis=row["discovery_basis"] or "",
        priority=row["priority"] or "medium",
        discovered_at=row["discovered_at"],
        status=row["status"] or "pending_confirmation",
        objective=row["objective"] if "objective" in keys and row["objective"] else "",
        acceptances=acceptances if isinstance(acceptances, list) else [],
    )


def init_discovered_tasks_db(db_path: str | Path, tasks: list[dict]) -> None:
    """初始化 SQLite db:建表 + 清空 + 批量插入。

    供 e2e 测试在运行前写入确定性 mock 数据。

    Args:
        db_path: SQLite db 文件路径。
        tasks: 任务 dict 列表(字段与 DiscoveredTask 对齐)。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # DROP+CREATE：每次初始化重建建表，保证 objective/acceptances 等新列就位
        # （CREATE TABLE IF NOT EXISTS 不会给已存在的旧库加列）。
        conn.executescript("DROP TABLE IF EXISTS discovered_tasks;\n" + _CREATE_TABLE_SQL)
        conn.executemany(
            "INSERT INTO discovered_tasks "
            "(task_id, bot_id, owner_id, dt, title, instruction, "
            " background, discovery_basis, priority, "
            " discovered_at, status, objective, acceptances) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            [
                (
                    t["task_id"],
                    t.get("bot_id", ""),
                    t.get("owner_id", ""),
                    t.get("dt", ""),
                    t.get("title", ""),
                    t.get("instruction", ""),
                    t.get("background", ""),
                    t.get("discovery_basis", ""),
                    t.get("priority", "medium"),
                    t.get("discovered_at"),
                    t.get("status", "pending_confirmation"),
                    t.get("objective", ""),
                    json.dumps(t.get("acceptances", []), ensure_ascii=False),
                )
                for t in tasks
            ],
        )
        conn.commit()
    finally:
        conn.close()


class TaskReader(Protocol):
    """任务数据读取接口。"""

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        """返回所有已发现的任务列表。"""
        ...


def upsert_discovered_tasks(db: DatabasePlugin, tasks: list[dict]) -> int:
    """通过 ORM upsert 已发现任务（写入接口）。

    按 ``task_id`` 自然键判断：已存在则更新字段，不存在则插入。
    跨 SQLite / OceanBase 兼容（纯 ORM 查询 + add/merge，无方言专有语法）。

    Args:
        db:    DatabasePlugin 实例（corp 走 OceanBase，local 走 SQLite 内存库）。
        tasks: 任务 dict 列表(字段与 DiscoveredTask 对齐)。

    Returns:
        写入的任务数量。
    """
    with db.transactional_orm_session() as session:
        for t in tasks:
            existing = (
                session.query(DiscoveredTaskModel)
                .filter(DiscoveredTaskModel.task_id == t["task_id"])
                .first()
            )
            if existing:
                existing.bot_id = t.get("bot_id", existing.bot_id)
                existing.owner_id = t.get("owner_id", existing.owner_id)
                existing.dt = t.get("dt", existing.dt)
                existing.title = t.get("title", existing.title)
                existing.instruction = t.get("instruction", existing.instruction)
                existing.background = t.get("background", existing.background)
                existing.discovery_basis = t.get("discovery_basis", existing.discovery_basis)
                existing.priority = t.get("priority", existing.priority)
                existing.discovered_at = t.get("discovered_at", existing.discovered_at)
                existing.status = t.get("status", existing.status)
                existing.objective = t.get("objective", existing.objective)
                existing.acceptances = json.dumps(
                    t.get("acceptances", []), ensure_ascii=False
                ) if "acceptances" in t else existing.acceptances
            else:
                row = DiscoveredTaskModel(
                    task_id=t["task_id"],
                    bot_id=t.get("bot_id", ""),
                    owner_id=t.get("owner_id", ""),
                    dt=t.get("dt", ""),
                    title=t.get("title", ""),
                    instruction=t.get("instruction", ""),
                    background=t.get("background", ""),
                    discovery_basis=t.get("discovery_basis", ""),
                    priority=t.get("priority", "medium"),
                    discovered_at=t.get("discovered_at"),
                    status=t.get("status", "pending_confirmation"),
                    objective=t.get("objective", ""),
                    acceptances=json.dumps(
                        t.get("acceptances", []), ensure_ascii=False
                    ),
                )
                session.add(row)
    logger.info("[task_discovery] upserted %d discovered tasks via ORM", len(tasks))
    return len(tasks)


def clear_discovered_tasks(db: DatabasePlugin) -> int:
    """清空所有已发现任务数据。

    供测试清理或运维重置使用。

    Args:
        db: DatabasePlugin 实例。

    Returns:
        删除的行数。
    """
    with db.transactional_orm_session() as session:
        count = session.query(DiscoveredTaskModel).delete()
    logger.info("[task_discovery] cleared %d discovered tasks", count)
    return count


def seed_discovered_tasks(db: DatabasePlugin, tasks: list[dict]) -> None:
    """清空 + 批量写入任务数据（供测试播种，保证幂等）。

    等价于 ``clear_discovered_tasks(db)`` + ``upsert_discovered_tasks(db, tasks)``。
    """
    clear_discovered_tasks(db)
    upsert_discovered_tasks(db, tasks)


class OrmTaskReader:
    """通过 ``DatabasePlugin.orm_session()`` 读取已发现任务。

    corp 环境走 OceanBase，local/singlebox 环境走 SQLite 内存库。
    替代 ``SqliteTaskReader`` 的直接 sqlite3 文件访问。
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        with self._db.orm_session() as session:
            rows = session.query(DiscoveredTaskModel).all()
            tasks = [row.to_domain() for row in rows]
        logger.info(
            "[task_discovery] read %d discovered tasks via ORM", len(tasks)
        )
        return tasks

    def read_pending_tasks(self) -> list[DiscoveredTask]:
        """只返回 ``pending_confirmation`` 状态的任务。"""
        return [t for t in self.read_discovered_tasks() if t.needs_confirmation]

    def read_pending_tasks_for_bot(
        self, bot_id: str, owner_id: str, dt: str,
    ) -> list[DiscoveredTask]:
        """返回指定 bot 当天的待确认任务。"""
        with self._db.orm_session() as session:
            rows = (
                session.query(DiscoveredTaskModel)
                .filter(
                    DiscoveredTaskModel.bot_id == bot_id,
                    DiscoveredTaskModel.owner_id == owner_id,
                    DiscoveredTaskModel.dt == dt,
                    DiscoveredTaskModel.status == "pending_confirmation",
                )
                .all()
            )
            tasks = [row.to_domain() for row in rows]
        logger.info(
            "[task_discovery] read %d pending tasks for bot=%s owner=%s dt=%s via ORM",
            len(tasks), bot_id, owner_id, dt,
        )
        return tasks


class SqliteTaskReader:
    """从本地 SQLite db 文件读取已发现任务（向后兼容，仅测试用）。

    db schema 由 ``init_discovered_tasks_db`` 创建;
    表名 ``discovered_tasks``,字段与 ``DiscoveredTask`` 一一对应。

    .. deprecated::
        生产环境请使用 ``OrmTaskReader``。此类仅保留供旧测试直接构造。

    使用方式::

        reader = SqliteTaskReader("scripts/.dependencies/data/discovered_tasks.db")
        tasks = reader.read_pending_tasks()
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        if not self._db_path.exists():
            logger.warning(
                "[task_discovery] db file not found: %s", self._db_path
            )
            return []

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(_SELECT_ALL_SQL)
            tasks = [_row_to_task(row) for row in cursor.fetchall()]
        except sqlite3.DatabaseError as exc:
            logger.error(
                "[task_discovery] failed to read %s: %s", self._db_path, exc
            )
            return []
        finally:
            conn.close()

        logger.info(
            "[task_discovery] read %d discovered tasks from %s",
            len(tasks), self._db_path,
        )
        return tasks

    def read_pending_tasks(self) -> list[DiscoveredTask]:
        """只返回 ``pending_confirmation`` 状态的任务。"""
        return [t for t in self.read_discovered_tasks() if t.needs_confirmation]

    def read_pending_tasks_for_bot(
        self, bot_id: str, owner_id: str, dt: str,
    ) -> list[DiscoveredTask]:
        """返回指定 bot 当天的待确认任务。"""
        if not self._db_path.exists():
            logger.warning(
                "[task_discovery] db file not found: %s", self._db_path
            )
            return []

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                _SELECT_PENDING_FOR_BOT_SQL, (bot_id, owner_id, dt),
            )
            tasks = [_row_to_task(row) for row in cursor.fetchall()]
        except sqlite3.DatabaseError as exc:
            logger.error(
                "[task_discovery] failed to read %s: %s", self._db_path, exc
            )
            return []
        finally:
            conn.close()

        logger.info(
            "[task_discovery] read %d pending tasks for bot=%s owner=%s dt=%s",
            len(tasks), bot_id, owner_id, dt,
        )
        return tasks


class MockTaskReader:
    """从本地 JSON 文件读取已发现任务的 mock 实现(向后兼容)。

    JSON 文件格式::

        {
          "tasks": [
            {
              "task_id": "discover_task_bot-001_user-001_2026-08-19",
              "bot_id": "bot-001",
              "owner_id": "user-001",
              "dt": "2026-08-19",
              "title": "...",
              "instruction": "...",
              "background": "...",
              "discovery_basis": "...",
              "priority": "high",
              "discovered_at": "2026-08-17T10:00:00Z",
              "status": "pending_confirmation"
            }
          ]
        }
    """

    def __init__(self, data_file: str | Path):
        self._data_file = Path(data_file)

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        if not self._data_file.exists():
            logger.warning(
                "[task_discovery] data file not found: %s", self._data_file
            )
            return []

        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("[task_discovery] failed to read %s: %s", self._data_file, exc)
            return []

        tasks_raw = raw.get("tasks", [])
        if not isinstance(tasks_raw, list):
            logger.error("[task_discovery] invalid data: 'tasks' is not a list")
            return []

        tasks: list[DiscoveredTask] = []
        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(
                    DiscoveredTask(
                        task_id=item["task_id"],
                        bot_id=item.get("bot_id", ""),
                        owner_id=item.get("owner_id", ""),
                        dt=item.get("dt", ""),
                        title=item["title"],
                        instruction=item.get("instruction", ""),
                        background=item.get("background", ""),
                        discovery_basis=item.get("discovery_basis", ""),
                        priority=item.get("priority", "medium"),
                        discovered_at=item.get("discovered_at"),
                        status=item.get("status", "pending_confirmation"),
                        objective=item.get("objective", ""),
                        acceptances=item.get("acceptances", []),
                    )
                )
            except KeyError as exc:
                logger.error(
                    "[task_discovery] skipping task: missing field %s in %s",
                    exc,
                    item,
                )

        logger.info("[task_discovery] read %d discovered tasks from %s", len(tasks), self._data_file)
        return tasks

    def read_pending_tasks(self) -> list[DiscoveredTask]:
        """只返回 ``pending_confirmation`` 状态的任务。"""
        return [t for t in self.read_discovered_tasks() if t.needs_confirmation]

    def read_pending_tasks_for_bot(
        self, bot_id: str, owner_id: str, dt: str,
    ) -> list[DiscoveredTask]:
        """返回指定 bot 当天的待确认任务。"""
        return [
            t for t in self.read_pending_tasks()
            if t.bot_id == bot_id and t.owner_id == owner_id and t.dt == dt
        ]


__all__ = [
    "TaskReader",
    "OrmTaskReader",
    "SqliteTaskReader",
    "MockTaskReader",
    "init_discovered_tasks_db",
    "upsert_discovered_tasks",
    "clear_discovered_tasks",
    "seed_discovered_tasks",
]