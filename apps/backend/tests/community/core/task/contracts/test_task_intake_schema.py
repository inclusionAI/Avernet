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


def _normalized_ddl() -> str:
    ddl = _DDL_PATH.read_text(encoding="utf-8")
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
