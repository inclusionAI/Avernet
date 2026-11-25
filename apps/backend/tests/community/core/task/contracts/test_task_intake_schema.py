"""OceanBase DDL contract for durable Task Intake and Relay delivery."""

from pathlib import Path
import re

from agentclaw.community.core.task.repository.models import TaskIntakeSessionModel


_DDL_PATH = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "task"
    / "sql"
    / "2026_10_12_task_intake.sql"
)
_EXECUTOR_DDL_PATH = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "task"
    / "sql"
    / "2026_10_25_task_executor_app_isolation.sql"
)


def _normalized_ddl() -> str:
    ddl = _DDL_PATH.read_text(encoding="utf-8")
    statements_only = re.sub(r"--[^\n]*", "", ddl)
    return re.sub(r"\s+", " ", statements_only)


def _normalized_executor_ddl() -> str:
    ddl = _EXECUTOR_DDL_PATH.read_text(encoding="utf-8")
    statements_only = re.sub(r"--[^\n]*", "", ddl)
    return re.sub(r"\s+", " ", statements_only)


def test_intake_session_has_required_audit_columns_in_orm_and_ddl() -> None:
    columns = TaskIntakeSessionModel.__table__.c
    assert "gmt_create" in columns
    assert "gmt_modified" in columns

    ddl = _normalized_ddl()
    assert (
        "`gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'"
    ) in ddl
    assert (
        "`gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间'"
    ) in ddl
    assert "ALTER TABLE `task_intake_session`" not in ddl


def test_task_info_intake_index_uses_odc_mysql_compatible_alter_table() -> None:
    ddl = _normalized_ddl()
    assert "ADD COLUMN `intake_session_id`" in ddl
    assert "ADD UNIQUE KEY `uk_task_info_intake_session` (`intake_session_id`)" in ddl
    assert "CREATE UNIQUE INDEX" not in ddl
    assert "ADD COLUMN IF NOT EXISTS" not in ddl


def test_delivery_intent_is_environment_scoped_in_ddl() -> None:
    """Relay delivery is only claimable by the environment that owns the task."""
    ddl = _normalized_ddl()
    assert "`env` varchar(32) NOT NULL DEFAULT 'pre'" in ddl
    assert (
        "KEY `idx_task_delivery_env_pending` (`env`, `state`, `lease_until_ms`)"
    ) in ddl


def test_task_state_is_executor_application_scoped_in_ddl() -> None:
    """Historical Relay rows default to agentclawengine and scans are app-scoped."""
    ddl = _normalized_executor_ddl()
    assert (
        "`executor_app` varchar(32) NOT NULL DEFAULT 'agentclawengine'"
    ) in ddl
    assert (
        "ADD KEY `idx_task_info_executor_app_env_status` "
        "(`executor_app`, `env`, `status`, `gmt_modified`)"
    ) in ddl
    assert (
        "ADD KEY `idx_task_delivery_executor_app_env_pending` "
        "(`executor_app`, `env`, `state`, `lease_until_ms`)"
    ) in ddl

    intake_ddl = _normalized_ddl()
    assert (
        "`executor_app` varchar(32) NOT NULL DEFAULT 'agentclawengine'"
    ) in intake_ddl
    assert (
        "KEY `idx_task_delivery_executor_app_env_pending` "
        "(`executor_app`, `env`, `state`, `lease_until_ms`)"
    ) in intake_ddl
