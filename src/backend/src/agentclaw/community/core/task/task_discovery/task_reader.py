"""TaskReader — 读取已发现的任务数据 (mock 实现)。

当前从本地 JSON 文件读取；未来可替换为真实数据源
（消息管线、行为分析平台等），只需实现同一 ``TaskReader`` Protocol。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from agentclaw.community.core.task.task_discovery.models import DiscoveredTask
from agentclaw.community.log import get_logger

logger = get_logger()


class TaskReader(Protocol):
    """任务数据读取接口。"""

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        """返回所有已发现的任务列表。"""
        ...


class MockTaskReader:
    """从本地 JSON 文件读取已发现任务的 mock 实现。

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


__all__ = ["TaskReader", "MockTaskReader"]
