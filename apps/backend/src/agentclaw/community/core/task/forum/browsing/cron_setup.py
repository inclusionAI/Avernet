"""Backend-side management of the OpenClaw cron task for the BBS Browse Loop.

For ``mode=openclaw`` (B), the Bot owns its local ``0 * * * *`` cron — but the
BACKEND creates it deterministically via the cron relay (one fixed name +
one fixed agentTurn payload) instead of asking the Bot's LLM to compose one.

Why backend-created instead of "tell the Bot to register":
* idempotent by the fixed name — a re-join updates the same-named task, never
  creates a duplicate (the earlier `bbs-browse-feed-reader` next to
  `bbs-browse-30min` came from the Bot freely naming each register);
* the payload is the same fixed text every time (no per-LLM-call variation);
* the cron still lives in the Bot's cron store, so it survives an Avernet
  backend restart — the B scheme's restart-independence is preserved.

The trigger carries the trusted Bot identity and Backend origin, while the
environment-selected browse Skill owns every concrete BBS API path. Join/unjoin of a
subscription delegates here so the "user explicitly enables the periodic task"
entrypoint owns the cron lifecycle alongside the subscription record.
"""

from __future__ import annotations

from typing import Any


from agentclaw.community.core.cron.cron_relay_service_protocol import (
    CronRelayServiceProtocol,
)
from agentclaw.community.core.task.forum.browsing.messages import (
    browse_cron_message,
)
from agentclaw.community.core.task.forum.models import BBS_BROWSE_LOOP_CRON_NAME
from agentclaw.community.log import get_logger

logger = get_logger()

# Fixed cron schedule + runtime for the BBS Browse Loop (B mode).
BBS_BROWSE_LOOP_CRON_EXPR = "0 * * * *"
BBS_BROWSE_LOOP_CRON_TIMEZONE = "Asia/Shanghai"
BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS = 600
BBS_BROWSE_LOOP_CRON_KIND = "agentTurn"

# The cron message must carry the trusted backend origin. The skill owns the
# concrete API paths, but it must not infer which local service hosts them.
BBS_BROWSE_RUN_TRIGGER = "[BBS-BROWSE]"

# The feature's cron-name namespace. Used to collapse legacy arbitrarily-named
# bbs-browse crons (e.g. `bbs-browse-feed-reader`, `bbs-browse-30min`) into the
# single canonical `bbs-browse-loop` task on (re)join, so a re-join never leaves
# duplicates behind.
_BBS_BROWSE_CRON_NAMESPACE = "bbs-browse"


def bbs_browse_loop_cron_body(*, bot_id: str, backend_base_url: str) -> dict[str, Any]:
    """The fixed cron body the backend upserts for an openclaw subscription."""
    if not bot_id:
        raise ValueError("bot_id is required in the BBS cron payload")
    if not backend_base_url.strip():
        raise ValueError("backend_base_url is required in the BBS cron payload")
    return {
        "name": BBS_BROWSE_LOOP_CRON_NAME,
        "schedule": BBS_BROWSE_LOOP_CRON_EXPR,
        "command": browse_cron_message(
            bot_id=bot_id,
            backend_base_url=backend_base_url,
        ),
        "timezone": BBS_BROWSE_LOOP_CRON_TIMEZONE,
        "enabled": True,
        "timeout_secs": BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS,
        "kind": BBS_BROWSE_LOOP_CRON_KIND,
    }


class BbsBrowseCronManager:
    """Create/remove the Bot-side hourly cron for an openclaw subscription.

    Idempotent by the fixed cron name. ``ensure_cron`` first deletes any
    legacy ``bbs-browse*`` cron on the bot, then creates or updates the
    canonical task — so repeated joins collapse to exactly one task and any
    pre-existing non-canonical bbs-browse crons are consolidated. ``remove_cron``
    deletes the canonical task and any legacy bbs-browse crons (best-effort),
    silently no-op'ing when none exist.
    """

    def __init__(
        self,
        *,
        cron_relay: CronRelayServiceProtocol,
        backend_base_url: str,
    ) -> None:
        self._cron = cron_relay
        self._backend_base_url = backend_base_url.rstrip("/")

    async def ensure_cron(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Create or repair the canonical bbs-browse cron for ``bot_id``.

        This operation is part of the subscription contract, not a best-effort
        side effect. If the draft runtime cannot list, create, or update the
        task, the caller must receive an error so startup cannot claim the
        periodic loop is initialized.
        """
        existing = await self._list(bot_id, owner_user_id)
        canonical: dict[str, Any] | None = None
        legacy: list[dict[str, Any]] = []
        for cron in existing:
            name = cron.get("name") or ""
            if name == BBS_BROWSE_LOOP_CRON_NAME:
                canonical = cron
            elif name.startswith(_BBS_BROWSE_CRON_NAMESPACE):
                legacy.append(cron)
        for cron in legacy:
            await self._delete(bot_id, owner_user_id, cron.get("id"))

        body = bbs_browse_loop_cron_body(
            bot_id=bot_id,
            backend_base_url=self._backend_base_url,
        )
        result: dict[str, Any]
        if canonical is not None:
            task_id = canonical.get("id")
            if not task_id:
                raise RuntimeError(
                    f"BBS Browse-Loop task has no update id: bot={bot_id}"
                )
            result = await self._cron.update_cron(
                bot_id=bot_id,
                user_id=owner_user_id,
                nick_name=owner_user_id,
                task_id=task_id,
                body=body,
            )
        else:
            result = await self._cron.create_cron(
                bot_id=bot_id,
                user_id=owner_user_id,
                nick_name=owner_user_id,
                body=body,
            )
        if not result.get("success"):
            raise RuntimeError(
                "BBS Browse-Loop runtime did not acknowledge cron setup: "
                f"bot={bot_id} result={result}"
            )
        return result

    async def remove_cron(self, *, bot_id: str, owner_user_id: str) -> None:
        """Delete the canonical (and any legacy) bbs-browse cron for ``bot_id``."""
        existing = await self._list(bot_id, owner_user_id)
        for cron in existing:
            name = cron.get("name") or ""
            if name == BBS_BROWSE_LOOP_CRON_NAME or name.startswith(
                _BBS_BROWSE_CRON_NAMESPACE
            ):
                await self._delete(bot_id, owner_user_id, cron.get("id"))

    async def _list(self, bot_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        result = await self._cron.list_all_crons(
            user_id=owner_user_id,
            nick_name=owner_user_id,
            bot_id=bot_id,
            runtime_stage="draft",
        )
        if not isinstance(result, dict) or not result.get("success"):
            raise RuntimeError(
                "BBS Browse-Loop draft runtime list failed: "
                f"bot={bot_id} result={result}"
            )
        failed_targets = result.get("failed_targets")
        if failed_targets:
            raise RuntimeError(
                "BBS Browse-Loop draft runtime has failed targets: "
                f"bot={bot_id} failed_targets={failed_targets}"
            )
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError(
                "BBS Browse-Loop draft runtime returned invalid task data: "
                f"bot={bot_id} result={result}"
            )
        return data

    async def _delete(
        self, bot_id: str, owner_user_id: str, task_id: str | None
    ) -> None:
        if not task_id:
            return
        try:
            await self._cron.delete_cron(
                bot_id=bot_id,
                user_id=owner_user_id,
                nick_name=owner_user_id,
                task_id=task_id,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "[bbs-browse-loop] delete cron bot=%s task=%s: %s",
                bot_id,
                task_id,
                exc,
            )
