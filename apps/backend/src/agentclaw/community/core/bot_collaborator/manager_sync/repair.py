"""Bounded maintenance submission for manager-sync backfill and repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.log import get_logger

logger = get_logger()
REPAIR_PAGE_MAX_KEYS = 100


@dataclass(frozen=True)
class RepairPage:
    """Admission results for one bounded repair page."""

    last_pk: int
    queued: int
    coalesced: int
    rejected: int
    rejected_keys: tuple[SyncKey, ...]


@runtime_checkable
class _SyncNotifier(Protocol):
    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult: ...


class ManagerSyncRepairService:
    """Request current Bot facts through the same bounded dispatcher."""

    def __init__(
        self,
        reader: ManagerSyncReadRepository,
        notifier: _SyncNotifier,
    ) -> None:
        self._reader = reader
        self._notifier = notifier

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        return self._notifier.request_sync(
            bot_pk=key.bot_pk,
            env=key.env,
            avernet_tenant=key.avernet_tenant,
            reason=SyncReason.REPAIR,
        )

    def request_page(
        self,
        *,
        avernet_tenant: str,
        env: str,
        after_pk: int,
        limit: int,
    ) -> RepairPage:
        bounded = max(0, min(int(limit), REPAIR_PAGE_MAX_KEYS))
        if bounded == 0:
            return RepairPage(after_pk, 0, 0, 0, ())
        keys = self._reader.page_keys(
            avernet_tenant=avernet_tenant,
            env=env,
            after_pk=after_pk,
            limit=bounded,
        )
        if not keys:
            return RepairPage(after_pk, 0, 0, 0, ())

        queued = 0
        coalesced = 0
        rejected_keys: list[SyncKey] = []
        for key in keys:
            result = self.request_bot(key)
            if result is SubmissionResult.QUEUED:
                queued += 1
            elif result is SubmissionResult.COALESCED:
                coalesced += 1
            else:
                rejected_keys.append(key)
        page = RepairPage(
            last_pk=keys[-1].bot_pk,
            queued=queued,
            coalesced=coalesced,
            rejected=len(rejected_keys),
            rejected_keys=tuple(rejected_keys),
        )
        logger.info(
            "[MANAGER-SYNC-REPAIR] page tenant=%s env=%s after_pk=%s "
            "last_pk=%s queued=%d coalesced=%d rejected=%d",
            avernet_tenant,
            env,
            after_pk,
            page.last_pk,
            page.queued,
            page.coalesced,
            page.rejected,
        )
        return page


class DisabledManagerSyncRepair(ManagerSyncRepairService):
    """Zero-resource disabled posture."""

    def __init__(self, reason: str = "disabled") -> None:
        self._reason = reason

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        return SubmissionResult.DISABLED

    def request_page(
        self,
        *,
        avernet_tenant: str,
        env: str,
        after_pk: int,
        limit: int,
    ) -> RepairPage:
        raise RuntimeError(f"manager sync repair is unavailable: {self._reason}")


__all__ = [
    "DisabledManagerSyncRepair",
    "ManagerSyncRepairService",
    "REPAIR_PAGE_MAX_KEYS",
    "RepairPage",
]
