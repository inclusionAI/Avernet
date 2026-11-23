"""Transactional create/complete contract for publication audit persistence."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot
from agentclaw.community.plugin_api.base import Plugin


class ServiceBotReleaseAuditPlugin(Plugin, Protocol):
    """Participate in the publication repository's separate post-commit audit transaction.

    The trusted in-process caller owns session creation, commit and rollback.
    Implementations must use that session and propagate persistence errors.
    The native operation has already committed; the caller rolls back only
    this audit transaction and logs the failure. Audit gaps have no automatic retry guarantee.
    Completion is idempotent and only updates the pending row for the exact
    change/publication/environment and expected stage in the restored tenant.
    This contract does not promise atomicity for an external SLS sink.
    """

    def create(self, session: Any, snapshot: ReleaseAuditSnapshot) -> None: ...

    def complete(
        self, session: Any, *, change_id: str, publish_id: int, env: str,
        expected_stage: str | None, finish_status: str,
        final_publish_status: str, finished_at: datetime,
    ) -> bool: ...
