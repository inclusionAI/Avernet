"""BbsBrowseLoopScheduler — APScheduler-driven per-Bot `*/30` cron (mode=framework).

Pattern mirrors ``TaskDiscoveryScheduler`` (BackgroundScheduler in a thread,
``asyncio.run`` at tick). Defaults to OFF: the scheduler is bound for lifecycle
discovery but only installs jobs when ``BBS_BROWSE_LOOP_AUTO_START_FRAMEWORK``
is ``true``. Subscriptions added/removed at runtime are registered on a
best-effort basis via :meth:`register_bot` / :meth:`unregister_bot`; future
phase wiring can call them from the subscription endpoints.
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
_AUTO_START_ENV = "BBS_BROWSE_LOOP_AUTO_START_FRAMEWORK"


class BbsBrowseLoopScheduler(LifecycleBase):
    """Per-Bot */30 cron driving A-mode Browse pushes."""

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
        """Lifecycle hook — start scheduler if env enables auto-start."""
        logger.debug("[bbs-browse-loop] → BbsBrowseLoopScheduler.startup()")
        enabled = os.environ.get(_AUTO_START_ENV, "false").strip().lower()
        if enabled not in ("true", "1", "yes", "on"):
            logger.info(
                "[bbs-browse-loop] framework cron disabled (%s != true)", _AUTO_START_ENV
            )
            self._scheduler = BackgroundScheduler()
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.start()
        # Restore jobs for all existing framework-mode subscriptions.
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
        """Add (or refresh) the `*/30` job for one Bot. No-op when disabled."""
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
        """Remove the job for one Bot. No-op if absent or scheduler disabled."""
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
