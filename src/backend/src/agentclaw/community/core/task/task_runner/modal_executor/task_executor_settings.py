"""TaskExecutor runtime policy readers."""

from __future__ import annotations

import logging

from agentclaw.community.core.task.domain.models import TaskNode

logger = logging.getLogger(__name__)


class TaskExecutorSettingsMixin:
    """Read result collection and execution-mode policy from injected settings."""

    def _skill_report_enabled(self) -> bool:
        """Return whether Bot results use the skill HTTP Push path."""
        task_settings = getattr(self, "_task_settings", None)
        if task_settings is None:
            return True
        try:
            return task_settings.is_enabled("skill_report_enabled")
        except Exception as exc:  # noqa: BLE001 unknown setting uses safe default
            logger.warning(
                "[task][task-executor] skill_report 读取失败 → 使用默认 Push: %s",
                exc,
            )
            return True

    def _node_skill_report_enabled(self, node: TaskNode) -> bool:
        graph = node.node_run_graph
        config = (
            graph.extend_props.get("execution_config", {}) if graph is not None else {}
        )
        return (
            config.get("orchestration_mode") == "relay" or self._skill_report_enabled()
        )

    def _relay_execution_enabled(self, task_id: str) -> bool:
        """Return whether the task graph is in distributed Relay mode."""
        if self._graph is None:
            return False
        try:
            snapshot = self._graph.query_task_dashboard(task_id)
        except Exception:  # noqa: BLE001 unavailable graph cannot be Relay here
            return False
        config = (getattr(snapshot, "extend_props", None) or {}).get(
            "execution_config"
        ) or {}
        return isinstance(config, dict) and config.get("orchestration_mode") == "relay"

    def _singlebot_2_group_enabled(self, task_id: str) -> bool:
        """Return whether single-Bot delivery should create an observer group."""
        if self._graph is None:
            logger.info(
                "[task][task-executor] singlebot_2_group 开关:graph 未接 → "
                "默认 True(走旁路) task=%s",
                task_id,
            )
            return True
        try:
            snapshot = self._graph.query_task_dashboard(task_id)
        except Exception:  # noqa: BLE001 unavailable graph uses safe default
            logger.warning(
                "[task][task-executor] singlebot_2_group 开关:graph 查询失败 → "
                "默认 True task=%s",
                task_id,
            )
            return True
        config = (getattr(snapshot, "extend_props", None) or {}).get(
            "execution_config"
        ) or {}
        if not isinstance(config, dict):
            logger.info(
                "[task][task-executor] singlebot_2_group 开关:execution_config "
                "非 dict → 默认 True task=%s",
                task_id,
            )
            return True
        value = config.get("singlebot_2_group", True)
        enabled = (
            value
            if isinstance(value, bool)
            else str(value).lower() not in ("false", "0", "no", "none", "")
        )
        logger.info(
            "[task][task-executor] singlebot_2_group "
            "开关:execution_config.singlebot_2_group=%s → enabled=%s task=%s",
            value,
            enabled,
            task_id,
        )
        return enabled
