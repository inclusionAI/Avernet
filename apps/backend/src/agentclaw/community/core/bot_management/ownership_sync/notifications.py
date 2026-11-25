"""Best-effort post-commit ownership hints and zero-resource disabled posture."""

from agentclaw.community.api.bot_ownership_sync import BotOwnershipSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
    SyncWorkKind,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def notify_bot_ownership_sync(
    sync: BotOwnershipSyncProtocol,
    *,
    bot_pk: int,
    env: str,
    avernet_tenant: str,
    reason: SyncReason,
) -> None:
    """Submit one committed ownership hint without changing the business result."""
    try:
        sync.request_ownership_sync(
            bot_pk=bot_pk,
            env=env,
            avernet_tenant=avernet_tenant,
            reason=reason,
        )
    except Exception as exc:
        logger.error(
            "bot ownership sync notification failed",
            extra={
                "bot_pk": bot_pk,
                "env": env,
                "avernet_tenant": avernet_tenant,
                "reason": reason.value,
                "exception_type": type(exc).__name__,
            },
        )


class DisabledBotOwnershipSync:
    """Retain finite admission diagnostics without reading or delivering facts."""

    def __init__(self) -> None:
        self._diagnostics = SyncDiagnostics()

    def request_ownership_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        self._diagnostics.record(
            SyncEvent.SUBMIT_ADMITTED,
            key=SyncKey(avernet_tenant, env, bot_pk),
            outcome=SubmissionResult.DISABLED,
            reason=reason,
            work_kind=SyncWorkKind.OWNERSHIP,
        )
        return SubmissionResult.DISABLED
