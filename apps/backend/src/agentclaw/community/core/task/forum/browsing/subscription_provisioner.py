"""Asynchronous provisioning for BBS periodic subscriptions.

The HTTP toggle must only persist a new subscription after the Bot-side
periodic tasks are assembled. This keeps ``ac_forum_browse_subscription`` a
truthful switch: if cron assembly fails during the temporary at-least-once
attempt, no new row is written and the user can retry.

Existing OpenClaw subscriptions are updated synchronously; their cron/skill
repair is best-effort because the subscription was already enabled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from agentclaw.community.core.task.forum.browsing.cron_setup import (
    BbsBrowseCronManager,
    BbsDigestCronManager,
)
from agentclaw.community.core.task.forum.browsing.runner import BbsBrowseLoopRunner
from agentclaw.community.core.task.forum.browsing.scheduler import (
    BbsBrowseLoopScheduler,
)
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
    BrowseSubscriptionRecord,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class BrowseSubscriptionProvisionResult:
    """The record returned to the caller plus its persistence state."""

    subscription: BrowseSubscriptionRecord
    created: bool
    persisted: bool


@dataclass
class _PendingSubscription:
    """Mutable pending request so retries can update the note before DB write."""

    bot_id: str
    owner_user_id: str
    note: str | None
    task: asyncio.Task[None] | None = None


class BbsBrowseSubscriptionProvisioner:
    """Provision Bot-side BBS tasks before persisting a new subscription toggle."""

    def __init__(
        self,
        *,
        service: ForumServiceProtocol,
        browse_cron: BbsBrowseCronManager,
        digest_cron: BbsDigestCronManager,
        runner: BbsBrowseLoopRunner,
        scheduler: BbsBrowseLoopScheduler,
    ) -> None:
        self._service = service
        self._browse_cron = browse_cron
        self._digest_cron = digest_cron
        self._runner = runner
        self._scheduler = scheduler
        self._pending: dict[str, _PendingSubscription] = {}

    def join_or_refresh(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        note: str | None,
    ) -> BrowseSubscriptionProvisionResult:
        """Return a response projection and trigger best-effort provisioning.

        A new subscription is not persisted in this call. The caller receives a
        202 projection; the background coroutine persists the row only after
        both Bot-side crons are successfully assembled.
        """
        old = self._service.get_subscription(bot_id=bot_id)

        if old is not None and old.mode == BROWSE_MODE_OPENCLAW:
            result = self._service.upsert_subscription(
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                mode=BROWSE_MODE_OPENCLAW,
                note=note,
            )
            self._schedule_repair(bot_id=bot_id, owner_user_id=owner_user_id)
            return BrowseSubscriptionProvisionResult(
                subscription=result.subscription,
                created=False,
                persisted=True,
            )

        pending = self._pending.get(bot_id)
        if pending is not None and pending.task is not None:
            pending.owner_user_id = owner_user_id
            pending.note = note
            return BrowseSubscriptionProvisionResult(
                subscription=self._provisional_record(
                    old=old,
                    bot_id=bot_id,
                    owner_user_id=owner_user_id,
                    note=note,
                ),
                created=old is None,
                persisted=False,
            )

        pending = _PendingSubscription(
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            note=note,
        )
        legacy_framework = old is not None and old.mode == BROWSE_MODE_FRAMEWORK
        pending.task = asyncio.create_task(
            self._provision(
                pending=pending,
                unregister_legacy_framework=legacy_framework,
            )
        )
        self._pending[bot_id] = pending
        return BrowseSubscriptionProvisionResult(
            subscription=self._provisional_record(
                old=old,
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                note=note,
            ),
            created=old is None,
            persisted=False,
        )

    async def cancel_pending(self, bot_id: str) -> None:
        """Cancel a pending toggle so a disable cannot be overridden later."""
        pending = self._pending.pop(bot_id, None)
        if pending is None or pending.task is None:
            return
        if not pending.task.done():
            pending.task.cancel()
        try:
            await pending.task
        except asyncio.CancelledError:
            pass

    def _schedule_repair(self, *, bot_id: str, owner_user_id: str) -> None:
        """Idempotently repair both crons and the activated skills.

        This path is used only for subscriptions that already exist and have an
        OpenClaw row. A transient failure leaves the existing subscription
        unchanged and is visible in logs for a later toggle/retry.
        """
        if bot_id in self._pending:
            return
        pending = _PendingSubscription(
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            note=None,
        )
        pending.task = asyncio.create_task(self._repair(pending))
        self._pending[bot_id] = pending

    async def _repair(self, pending: _PendingSubscription) -> None:
        bot_id = pending.bot_id
        try:
            await self._assemble(pending)
            logger.info(
                "[bbs-subscription] asynchronous repair completed bot=%s", bot_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — an existing subscription must remain usable
            logger.warning(
                "[bbs-subscription] asynchronous repair failed bot=%s: %s",
                bot_id,
                exc,
            )
        finally:
            if self._pending.get(bot_id) is pending:
                self._pending.pop(bot_id, None)

    async def _provision(
        self,
        *,
        pending: _PendingSubscription,
        unregister_legacy_framework: bool,
    ) -> None:
        bot_id = pending.bot_id
        try:
            await self._assemble(pending)
            result = self._service.upsert_subscription(
                bot_id=bot_id,
                owner_user_id=pending.owner_user_id,
                mode=BROWSE_MODE_OPENCLAW,
                note=pending.note,
            )
            if unregister_legacy_framework:
                self._scheduler.unregister_bot(bot_id)
            logger.info(
                "[bbs-subscription] asynchronous provision persisted bot=%s created=%s",
                bot_id,
                result.created,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — explicit no-row-on-failure policy
            logger.warning(
                "[bbs-subscription] asynchronous provision failed bot=%s: %s",
                bot_id,
                exc,
            )
        finally:
            if self._pending.get(bot_id) is pending:
                self._pending.pop(bot_id, None)

    async def _assemble(self, pending: _PendingSubscription) -> None:
        await self._browse_cron.ensure_cron(
            bot_id=pending.bot_id,
            owner_user_id=pending.owner_user_id,
        )
        await self._digest_cron.ensure_cron(
            bot_id=pending.bot_id,
            owner_user_id=pending.owner_user_id,
        )
        await self._runner.push_install_bbs_skills(
            bot_id=pending.bot_id,
            owner_user_id=pending.owner_user_id,
        )

    def _provisional_record(
        self,
        *,
        old: BrowseSubscriptionRecord | None,
        bot_id: str,
        owner_user_id: str,
        note: str | None,
    ) -> BrowseSubscriptionRecord:
        now = datetime.now(timezone.utc)
        if old is None:
            return BrowseSubscriptionRecord(
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                mode=BROWSE_MODE_OPENCLAW,
                note=note,
                created_at=now,
                updated_at=now,
            )
        return replace(
            old,
            mode=BROWSE_MODE_OPENCLAW,
            note=note,
            updated_at=now,
        )


__all__ = [
    "BbsBrowseSubscriptionProvisioner",
    "BrowseSubscriptionProvisionResult",
]
