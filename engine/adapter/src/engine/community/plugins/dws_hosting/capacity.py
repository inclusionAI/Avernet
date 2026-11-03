"""Admission bounds checked before durable intake; never drop on overflow."""

import shutil

from engine.community.kernel.dws_runtime import HostingCapacityError, RuntimePolicy


def check_disk(path, policy: RuntimePolicy) -> None:
    if path.stat().st_size >= policy.max_database_bytes:
        raise HostingCapacityError("database_capacity")
    if shutil.disk_usage(path.parent).free < policy.min_free_bytes:
        raise HostingCapacityError("disk_reserve")


def check_intake(db, path, policy: RuntimePolicy, *, hint: bool = False) -> None:
    check_disk(path, policy)
    if hint:
        count = db.execute("SELECT COUNT(*) FROM intake_hints").fetchone()[0]
        if count >= policy.max_hints:
            raise HostingCapacityError("hint_capacity")
    else:
        count = db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
        if count >= policy.max_inbox:
            raise HostingCapacityError("inbox_capacity")
        count = db.execute(
            "SELECT COUNT(*) FROM batches WHERE state NOT IN ('completed', 'cancelled')"
        ).fetchone()[0]
        if count >= policy.max_pending:
            raise HostingCapacityError("backlog_capacity")
