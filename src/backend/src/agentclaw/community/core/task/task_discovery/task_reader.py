"""TaskReader — 读取已发现的任务数据 (mock 实现)。

``SqliteTaskReader`` 从本地 SQLite db 文件读取；
``MockTaskReader`` 从本地 JSON 文件读取(向后兼容)。
未来可替换为真实数据源(消息管线、行为分析平台等)，
只需实现同一 ``TaskReader`` Protocol。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from agentclaw.community.core.task.task_discovery.models import DiscoveredTask
from agentclaw.community.log import get_logger

logger = get_logger()

#: 建表 DDL
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS discovered_tasks (
    task_id            TEXT PRIMARY KEY,
    project_name       TEXT NOT NULL,
    description        TEXT,
    business_scenario  TEXT,
    discovery_basis    TEXT,
    work_item_url      TEXT,
    priority           TEXT DEFAULT 'medium',
    discovered_at      TEXT,
    status             TEXT DEFAULT 'pending_confirmation'
);
"""

#: 查询全量任务的 SQL
_SELECT_ALL_SQL = "SELECT task_id, project_name, description, business_scenario, discovery_basis, work_item_url, priority, discovered_at, status FROM discovered_tasks;"


def _row_to_task(row: sqlite3.Row) -> DiscoveredTask:
    """将 sqlite3.Row 映射为 DiscoveredTask。"""
    return DiscoveredTask(
        task_id=row["task_id"],
        project_name=row["project_name"],
        description=row["description"] or "",
        business_scenario=row["business_scenario"] or "",
        discovery_basis=row["discovery_basis"] or "",
        work_item_url=row["work_item_url"],
        priority=row["priority"] or "medium",
        discovered_at=row["discovered_at"],
        status=row["status"] or "pending_confirmation",
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
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute("DELETE FROM discovered_tasks;")
        conn.executemany(
            "INSERT INTO discovered_tasks "
            "(task_id, project_name, description, business_scenario, "
            " discovery_basis, work_item_url, priority, discovered_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            [
                (
                    t["task_id"],
                    t["project_name"],
                    t.get("description", ""),
                    t.get("business_scenario", ""),
                    t.get("discovery_basis", ""),
                    t.get("work_item_url"),
                    t.get("priority", "medium"),
                    t.get("discovered_at"),
                    t.get("status", "pending_confirmation"),
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


class SqliteTaskReader:
    """从本地 SQLite db 文件读取已发现任务。

    db schema 由 ``init_discovered_tasks_db`` 创建;
    表名 ``discovered_tasks``,字段与 ``DiscoveredTask`` 一一对应。

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


class MockTaskReader:
    """从本地 JSON 文件读取已发现任务的 mock 实现(向后兼容)。

    JSON 文件格式::

        {
          "tasks": [
            {
              "task_id": "task-001",
              "project_name": "...",
              "description": "...",
              "business_scenario": "...",
              "discovery_basis": "...",
              "work_item_url": "...",
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
                        project_name=item["project_name"],
                        description=item.get("description", ""),
                        business_scenario=item.get("business_scenario", ""),
                        discovery_basis=item.get("discovery_basis", ""),
                        work_item_url=item.get("work_item_url"),
                        priority=item.get("priority", "medium"),
                        discovered_at=item.get("discovered_at"),
                        status=item.get("status", "pending_confirmation"),
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


__all__ = [
    "TaskReader",
    "SqliteTaskReader",
    "MockTaskReader",
    "init_discovered_tasks_db",
]
