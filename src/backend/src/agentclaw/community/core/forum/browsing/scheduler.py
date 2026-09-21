"""BbsBrowseLoopScheduler — APScheduler-driven per-Bot `*/30` cron (mode=framework).

The subscription (`ac_forum_browse_subscription` with ``mode=framework``) IS
the opt-in: a Bot subscribes once and the backend owns a `*/30` APScheduler
job that pushes one Browse message to it on every tick. No env gate — the
scheduler starts at app boot and registers a job for every existing
framework-mode subscription. New subscriptions added at runtime are picked up
via :meth:`register_bot` (wired in the subscription endpoints); deletes via
:meth:`unregister_bot`. ``mode=openclaw`` is the OpenClaw-self-cron path and
never goes through this scheduler.

Pattern mirrors ``TaskDiscoveryScheduler`` (BackgroundScheduler in a thread,
``asyncio.run`` at tick). Cron expr / timezone are overridable via
``BBS_BROWSE_LOOP_CRON`` / ``BBS_BROWSE_LOOP_TIMEZONE`` (verification can set
``*/1 * * * *`` to shorten intervals; restore `*/30 * * * *` afterwards).
"""

from __future__ import annotations

import asyncio
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from injector import inject

from agentclaw.community.core.forum.browsing.runner import BbsBrowseLoopRunner
from agentclaw.community.core.forum.models import BROWSE_MODE_FRAMEWORK
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

_DEFAULT_CRON = "*/30 * * * *"
_DEFAULT_TIMEZONE = "Asia/Shanghai"


class BbsBrowseLoopScheduler(LifecycleBase):
    """Per-Bot */30 cron driving A-mode Browse pushes — autostart, no env gate."""

    @inject
    def __init__(
        self,
        runner: BbsBrowseLoopRunner,
        service: ForumServiceProtocol,
    ) -> None:
        self._runner = runner
        self._service = service
        self._scheduler: BackgroundScheduler | None = None
        self._cron_expr = os.environ.get("BBS_BROWSE_LOOP_CRON", _DEFAULT_CRON)
        self._tz = os.environ.get("BBS_BROWSE_LOOP_TIMEZONE", _DEFAULT_TIMEZONE)

    async def startup(self) -> None:
        """Lifecycle hook — always start scheduler and register existing framework subs.

        Subscribing (`ac_forum_browse_subscription` row with mode=framework) is
        the opt-in. No env gate here; the scheduler starts unconditionally at
        app boot and registers a `*/30` job for each framework-mode subscription
        present in the table so ticks begin before the next cron boundary.
        """
        logger.debug("[bbs-browse-loop] → BbsBrowseLoopScheduler.startup()")
        self._scheduler = BackgroundScheduler()
        self._scheduler.start()
        page = self._service.list_subscriptions(page=1, page_size=200, mode=BROWSE_MODE_FRAMEWORK)
        for sub in page.items:
            self.register_bot(sub.bot_id)
        logger.info(
            "[bbs-browse-loop] scheduler started — cron='%s' tz='%s' jobs=%d",
            self._cron_expr, self._tz, len(page.items),
        )

    async def shutdown(self) -> None:
        """Lifecycle hook — stop scheduler."""
        logger.debug("[bbs-browse-loop] → BbsBrowseLoopScheduler.shutdown()")
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("[bbs-browse-loop] scheduler stopped")

    def register_bot(self, bot_id: str) -> None:
        """Add (or refresh) the `*/30` job for one Bot. No-op before startup."""
        if self._scheduler is None:
            return
        self._scheduler.add_job(
            self._push_for_bot,
            CronTrigger.from_crontab(self._cron_expr, timezone=self._tz),
            id=f"bbs-browse-framework-{bot_id}",
            args=(bot_id,),
            replace_existing=True,
        )

    def unregister_bot(self, bot_id: str) -> None:
        """Remove the job for one Bot. No-op if absent or before startup."""
        if self._scheduler is None:
            return
        try:
            self._scheduler.remove_job(f"bbs-browse-framework-{bot_id}")
        except Exception:  # noqa: BLE001 — best-effort removal on shutdown
            logger.warning("[bbs-browse-loop] unregister bot=%s job missing", bot_id)

    def _push_for_bot(self, bot_id: str) -> None:
        """Per-job sync callback — runs one Browse push in its own event loop."""
        try:
            asyncio.run(
                self._runner.push_browse_once(
                    bot_id=bot_id, expected_mode=BROWSE_MODE_FRAMEWORK
                )
            )
        except Exception as exc:  # noqa: BLE001 — tick failures must not kill the scheduler
            logger.warning("[bbs-browse-loop] tick bot=%s failed: %s", bot_id, exc)
