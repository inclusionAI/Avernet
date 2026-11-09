"""Optional durable backup context, independent of restart's mutation state."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable


class BackupPersistenceError(RuntimeError):
    """Saving backup policy/evidence failed; never downgrade this to best effort."""


@dataclass
class BackupOperation:
    operation_id: str
    started_at: float
    confirmed: set[str] = field(default_factory=set)
    persist: Callable[..., None] = lambda **changes: None

    def confirm(self, target: str) -> None:
        if target not in self.confirmed:
            confirmed = self.confirmed | {target}
            self._save(fastdisk_targets=sorted(confirmed))
            self.confirmed = confirmed

    def warn(self) -> None:
        self._save(
            warning={
                "code": "BACKUP_FAILED_CONTINUE",
                "message": "备份失败，按配置继续回收，存在数据丢失风险",
            }
        )

    def _save(self, **changes) -> None:
        try:
            self.persist(**changes)
        except Exception as exc:
            raise BackupPersistenceError("Backup state could not be saved") from exc


current_backup: ContextVar[BackupOperation | None] = ContextVar(
    "durable_backup", default=None
)


def backup_operation_type() -> str:
    return "recycle" if current_backup.get() is not None else "restart"


def backup_deadline(
    *, started, ordinary_budget, fastdisk_budget, required, strict_deadline
):
    import time

    from .restart_state import BACKUP_TIMEOUT, current_restart

    backup, execution = current_backup.get(), current_restart.get()
    first_started = (
        backup.started_at
        if backup is not None
        else execution.payload["started_at"]
        if execution is not None
        else time.time()
    )
    remaining = (
        BACKUP_TIMEOUT - (time.time() - first_started)
        if backup is not None or execution is not None
        else ordinary_budget
    )
    deadline = started + remaining
    if required:
        deadline = min(
            deadline,
            started + fastdisk_budget - (time.time() - first_started),
            strict_deadline if strict_deadline is not None else float("inf"),
        )
    return deadline
