"""Bounded manual resubmission of current ownership through ordinary admission."""

from agentclaw.community.api.bot_ownership_sync import BotOwnershipSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
    REPAIR_PAGE_MAX_KEYS,
    RepairPage,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)


class OwnershipSyncRepairService:
    """Submit scoped keys without overriding Owners or persisting delivery state."""

    def __init__(
        self,
        reader: OwnershipSyncReadRepository,
        notifier: BotOwnershipSyncProtocol,
    ) -> None:
        self._reader = reader
        self._notifier = notifier

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        return self._notifier.request_ownership_sync(
            bot_pk=key.bot_pk,
            env=key.env,
            avernet_tenant=key.avernet_tenant,
            reason=SyncReason.REPAIR,
        )

    def request_page(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int,
    ) -> RepairPage:
        bounded = max(0, min(limit, REPAIR_PAGE_MAX_KEYS))
        if bounded == 0:
            return RepairPage(after_pk, 0, 0, 0, ())
        keys = self._reader.page_keys(
            avernet_tenant=avernet_tenant,
            env=env,
            after_pk=after_pk,
            limit=bounded,
        )
        if any(key.avernet_tenant != avernet_tenant or key.env != env for key in keys):
            raise ValueError("ownership_repair_scope_mismatch")

        queued = coalesced = 0
        rejected: list[SyncKey] = []
        for key in keys:
            result = self.request_bot(key)
            if result is SubmissionResult.QUEUED:
                queued += 1
            elif result is SubmissionResult.COALESCED:
                coalesced += 1
            else:
                rejected.append(key)
        return RepairPage(
            last_pk=keys[-1].bot_pk if keys else after_pk,
            queued=queued,
            coalesced=coalesced,
            rejected=len(rejected),
            rejected_keys=tuple(rejected),
        )


class DisabledOwnershipSyncRepair(OwnershipSyncRepairService):
    """Zero-resource disabled posture; never reports a successful repair page."""

    def __init__(self) -> None:
        pass

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        return SubmissionResult.DISABLED

    def request_page(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int,
    ) -> RepairPage:
        raise RuntimeError("ownership sync repair unavailable")
