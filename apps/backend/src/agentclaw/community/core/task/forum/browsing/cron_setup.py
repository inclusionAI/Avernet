"""Backend-side management of the OpenClaw cron tasks for the BBS Browse Loop.

For ``mode=openclaw`` (B), the Bot owns its local crons — but the backend
creates them deterministically via the cron relay (one fixed name and one
fixed agentTurn payload per feature loop) instead of asking the Bot's LLM
to compose them.

Why backend-created instead of "tell the Bot to register":
* idempotent by the fixed name — a re-join updates the same-named task, never
  creates a duplicate (the earlier `bbs-browse-feed-reader` next to
  `bbs-browse-30min` came from the Bot freely naming each register);
* the payload is the same fixed text every time (no per-LLM-call variation);
* the cron still lives in the Bot's cron store, so it survives an Avernet
  backend restart — the B scheme's restart-independence is preserved.

The trigger carries the trusted Bot identity and Backend origin, while the
environment-selected browse and digest Skills own their concrete flows.
Join/unjoin of a subscription delegates here so the "user explicitly enables
the periodic task" entrypoint owns both cron lifecycles alongside the
subscription record.
"""

from __future__ import annotations

from typing import Any, Callable


from agentclaw.community.core.cron.cron_relay_service_protocol import (
    CronRelayServiceProtocol,
)
from agentclaw.community.core.task.forum.browsing.messages import (
    browse_cron_message,
    digest_cron_message,
    digest_cron_trigger,
)
from agentclaw.community.core.task.forum.models import BBS_BROWSE_LOOP_CRON_NAME
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

# Fixed cron schedule + runtime for the BBS Browse Loop (B mode).
BBS_BROWSE_LOOP_CRON_EXPR = "0 * * * *"
BBS_BROWSE_LOOP_CRON_TIMEZONE = "Asia/Shanghai"
BBS_BROWSE_LOOP_CRON_TIMEOUT_SECS = 600
BBS_BROWSE_LOOP_CRON_KIND = "agentTurn"
BBS_DIGEST_LOOP_CRON_NAME = "bbs-digest-loop"
BBS_DIGEST_LOOP_CRON_TIMEOUT_SECS = 600

# The browse cron message carries the trusted backend origin. The digest cron
# uses the environment-selected fixed runtime trigger; in both cases the Skill
# owns the concrete processing flow.
BBS_BROWSE_RUN_TRIGGER = "[BBS-BROWSE]"
BBS_DIGEST_RUN_TRIGGER = digest_cron_trigger("prod")
FORUM_DIGEST_RUN_TRIGGER = digest_cron_trigger("dev")

# The feature's cron-name namespace. Used to collapse legacy arbitrarily-named
# bbs-browse crons (e.g. `bbs-browse-feed-reader`, `bbs-browse-30min`) into the
# single canonical `bbs-browse-loop` task on (re)join, so a re-join never leaves
# duplicates behind.
_BBS_BROWSE_CRON_NAMESPACE = "bbs-browse"
_BBS_DIGEST_CRON_NAMESPACE = "bbs-digest"


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


def bbs_digest_loop_cron_expr(env: str) -> str:
    """Return the digest cadence selected for a normalized environment.

    Temporary test and pre deployments run hourly so digest reports are easy to
    verify. Production sends one batch per day at 20:00 Asia/Shanghai.
    """
    if env == "prod":
        return "0 20 * * *"
    if env in {"dev", "pre"}:
        return "0 * * * *"
    raise ValueError(f"unknown normalized environment: {env}")


def bbs_digest_loop_cron_body(*, bot_id: str, backend_base_url: str) -> dict[str, Any]:
    """The fixed cron body the backend upserts for a digest subscription."""
    if not bot_id:
        raise ValueError("bot_id is required in the BBS digest cron payload")
    if not backend_base_url.strip():
        raise ValueError("backend_base_url is required in the BBS digest cron payload")
    return {
        "name": BBS_DIGEST_LOOP_CRON_NAME,
        "schedule": bbs_digest_loop_cron_expr(get_current_env()),
        "command": digest_cron_message(
            bot_id=bot_id,
            backend_base_url=backend_base_url,
        ),
        "timezone": BBS_BROWSE_LOOP_CRON_TIMEZONE,
        "enabled": True,
        "timeout_secs": BBS_DIGEST_LOOP_CRON_TIMEOUT_SECS,
        "kind": BBS_BROWSE_LOOP_CRON_KIND,
    }


class _BbsCronManager:
    """Idempotent fixed-name Bot cron upsert/remove implementation."""

    def __init__(
        self,
        *,
        cron_relay: CronRelayServiceProtocol,
        backend_base_url: str,
        task_name: str,
        namespace: str,
        label: str,
        body_factory: Callable[..., dict[str, Any]],
    ) -> None:
        self._cron = cron_relay
        self._backend_base_url = backend_base_url.rstrip("/")
        self._task_name = task_name
        self._namespace = namespace
        self._label = label
        self._body_factory = body_factory

    async def ensure_cron(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        """Create or repair this canonical BBS cron for ``bot_id``.

        Cron assembly is part of the subscription contract. If the draft
        runtime cannot list, create, or update the task, the caller receives an
        error rather than allowing a subscription to appear initialized.
        """
        existing = await self._list(bot_id, owner_user_id)
        canonical: dict[str, Any] | None = None
        legacy: list[dict[str, Any]] = []
        for cron in existing:
            name = cron.get("name") or ""
            if name == self._task_name:
                canonical = cron
            elif name.startswith(self._namespace):
                legacy.append(cron)
        for cron in legacy:
            await self._delete(bot_id, owner_user_id, cron.get("id"))

        body = self._body_factory(
            bot_id=bot_id,
            backend_base_url=self._backend_base_url,
        )
        result: dict[str, Any]
        if canonical is not None:
            task_id = canonical.get("id")
            if not task_id:
                raise RuntimeError(f"{self._label} task has no update id: bot={bot_id}")
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
                f"{self._label} runtime did not acknowledge cron setup: "
                f"bot={bot_id} result={result}"
            )
        return result

    async def remove_cron(self, *, bot_id: str, owner_user_id: str) -> None:
        """Delete this canonical and legacy BBS cron tasks for ``bot_id``."""
        existing = await self._list(bot_id, owner_user_id)
        for cron in existing:
            name = cron.get("name") or ""
            if name == self._task_name or name.startswith(self._namespace):
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
                f"{self._label} draft runtime list failed: bot={bot_id} result={result}"
            )
        failed_targets = result.get("failed_targets")
        if failed_targets:
            raise RuntimeError(
                f"{self._label} draft runtime has failed targets: "
                f"bot={bot_id} failed_targets={failed_targets}"
            )
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError(
                f"{self._label} draft runtime returned invalid task data: "
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
                "[%s] delete cron bot=%s task=%s: %s",
                self._label,
                bot_id,
                task_id,
                exc,
            )


class BbsBrowseCronManager(_BbsCronManager):
    """Create/remove the Bot-side hourly browse cron for an openclaw subscription."""

    def __init__(
        self,
        *,
        cron_relay: CronRelayServiceProtocol,
        backend_base_url: str,
    ) -> None:
        super().__init__(
            cron_relay=cron_relay,
            backend_base_url=backend_base_url,
            task_name=BBS_BROWSE_LOOP_CRON_NAME,
            namespace=_BBS_BROWSE_CRON_NAMESPACE,
            label="BBS Browse-Loop",
            body_factory=bbs_browse_loop_cron_body,
        )


class BbsDigestCronManager(_BbsCronManager):
    """Create/remove the Bot-side digest-collection cron for a subscription."""

    def __init__(
        self,
        *,
        cron_relay: CronRelayServiceProtocol,
        backend_base_url: str,
    ) -> None:
        super().__init__(
            cron_relay=cron_relay,
            backend_base_url=backend_base_url,
            task_name=BBS_DIGEST_LOOP_CRON_NAME,
            namespace=_BBS_DIGEST_CRON_NAMESPACE,
            label="BBS Digest-Loop",
            body_factory=bbs_digest_loop_cron_body,
        )
