"""Repository protocol for the `ac_bot_copy` operation ledger.

One protocol; the SQLAlchemy impl in `sqlalchemy_impl.py` runs unchanged on
prod OceanBase and local SQLite (the same "unified repository" pattern as
`task_queue`). Bound directly in DI; no `@plugin_impl`.

The repository is a **ledger** only under derive-on-read: `insert` writes a row
at submission, `get_*` reads it for the derive helper and the FE-facing
`copy_state` field, `cas_retry` is the operator-retry throttle, and
`update_progress_ext` lets Stage B's materialisers record step intermediate
state. There is no `cas_status` — live status is derived from
`ac_bot_config_manifest_apply` + `ac_bots.status` by `compute_copy_status`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_copy.types import BotCopyRow


@runtime_checkable
class BotCopyRepositoryProtocol(Protocol):
    """The `ac_bot_copy` ledger operations the copy flow needs."""

    def insert(self, row: BotCopyRow) -> None:
        """Persist a new operation ledger row. Idempotent on ``(env, operation_id)`` UNIQUE."""
        ...

    def get_by_operation_id(self, *, env: str, operation_id: str) -> BotCopyRow | None:
        """The ledger row for one operation, or ``None``."""
        ...

    def get_by_dst_bot_id(self, *, env: str, dst_bot_id: str) -> BotCopyRow | None:
        """The ledger row whose dst is this bot (single-bot status derive)."""
        ...

    def get_many_by_dst_bot_ids(
        self, *, env: str, dst_bot_ids: Sequence[str]
    ) -> Mapping[str, BotCopyRow]:
        """Ledger rows keyed by dst_bot_id, batched in one SELECT (no N+1) for `/bots/all`.

        Missing dst_bot_ids simply do not appear in the result.
        """
        ...

    def cas_retry(
        self, *, env: str, operation_id: str, now: datetime, throttle_seconds: int = 60
    ) -> bool:
        """Throttled operator retry stamp.

        Atomically: if ``status NOT IN terminal`` AND
        (``last_retry_at IS NULL`` OR ``last_retry_at < now - throttle_seconds``),
        set ``last_retry_at = now`` and ``retries = retries + 1``. Returns whether
        the CAS landed. Guards against operator retry spam and refuses terminal rows.
        """
        ...

    def update_progress_ext(
        self, *, env: str, operation_id: str, progress_ext: Mapping[str, Any]
    ) -> None:
        """Overwrite the row's ``progress_ext`` JSON with step intermediate state."""
        ...


__all__ = ["BotCopyRepositoryProtocol"]
