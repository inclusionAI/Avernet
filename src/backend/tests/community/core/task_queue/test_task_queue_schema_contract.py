"""Contract checks keeping the provisioning DDL aligned with the ORM model.

``repository/models.py`` is the source of truth for this table's schema and
``sql/2026_09_06_task_queue.sql`` is a reference derived from it, so the failure
mode worth guarding is the two drifting apart — specifically an index the ORM
declares going missing from the DDL. That is not hypothetical: the first draft
of the DDL deliberately omitted the legacy dedup index on the reasoning that a
new table should not gain a scope wider than the code's, which would have left a
freshly provisioned OceanBase database accepting an enqueue that
``test_same_key_under_different_app_is_rejected_while_the_legacy_index_lives``
asserts is rejected. Nothing caught it; this does.

The DDL is read as text rather than executed because its OceanBase-only
modifiers (``GLOBAL``, ``AUTO_INCREMENT_MODE``) are not parseable by the SQLite
engine the suite runs on — which is the same reason they cannot be expressed in
the ORM and have to live in the file at all.
"""

from pathlib import Path
import re

from sqlalchemy import Index

from agentclaw.community.core.task_queue.repository.models import TaskQueueModel


_DDL_PATH = (
    Path(__file__).parents[4]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "task_queue"
    / "sql"
    / "2026_09_06_task_queue.sql"
)


def _ddl_without_comments() -> str:
    return "\n".join(
        line
        for line in _DDL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )


def _orm_unique_indexes() -> dict[str, tuple[str, ...]]:
    return {
        item.name: tuple(column.name for column in item.columns)
        for item in TaskQueueModel.__table__.indexes
        if isinstance(item, Index) and item.unique
    }


def _ddl_unique_indexes(ddl: str) -> dict[str, tuple[str, ...]]:
    found = {}
    for name, columns in re.findall(
        r"UNIQUE KEY\s+`([^`]+)`\s*\(([^)]*)\)", ddl, flags=re.DOTALL
    ):
        found[name] = tuple(re.findall(r"`([^`]+)`", columns))
    return found


def test_ddl_declares_every_unique_index_the_orm_does() -> None:
    """Both dedup indexes, with the same columns in the same order.

    Column order is part of the assertion because a unique index is a different
    constraint under a different order, and the dedup scope is exactly what
    these two indexes disagree about.
    """
    assert _ddl_unique_indexes(_ddl_without_comments()) == _orm_unique_indexes()


def test_unique_indexes_are_global() -> None:
    """``GLOBAL`` is the one property the ORM cannot express.

    Without it a partitioned table allows the same active idempotency key once
    per partition, which defeats enqueue dedup silently rather than loudly.
    """
    ddl = _ddl_without_comments()
    for name in _orm_unique_indexes():
        clause = re.search(
            rf"UNIQUE KEY\s+`{re.escape(name)}`\s*\([^)]*\)\s*(\w+)",
            ddl,
            flags=re.DOTALL,
        )
        assert clause is not None, f"{name} missing from the DDL"
        assert clause.group(1) == "GLOBAL", f"{name} is not declared GLOBAL"


def test_auto_increment_mode_is_pinned_to_order() -> None:
    """``_find_by_key`` reads the audit row with ``ORDER BY id DESC``.

    Under NOORDER each observer caches its own auto-increment range, so ids stop
    being monotonic in insertion order and that query answers with an older row.
    """
    assert "AUTO_INCREMENT_MODE = 'ORDER'" in _ddl_without_comments()
