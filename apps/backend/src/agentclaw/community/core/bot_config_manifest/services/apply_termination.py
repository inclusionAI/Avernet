"""Terminating an apply that cannot, or did not, run (W4; split out with W8).

Two paths end an apply without the orchestrator ever finishing it: the launch
failed after the ``RUNNING`` row was written, or the task handler could not
rebuild the context it needs. Both must leave a terminal record and a released
lock — without the record the row polls ``RUNNING`` until its lock goes stale,
without the release the bot is locked against every future apply for the whole
TTL. The two writers live here, beside the one rule they share: the failure's
text goes to the log, never into the stored report.

Split out of ``config_manifest_apply_service`` for size (the 1000-line cap);
it is the part of that module that is one cohesive concern.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def parse_started_at(value: Optional[str]) -> datetime:
    """The apply's own start time, or now if the payload predates the field."""
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


def record_engine_failure(report: ApplyReport, exc: Exception) -> None:
    """Log the cause; the stored report says FAILED with no entries.

    Deliberately not putting ``str(exc)`` in the report: an exception from
    the engine itself is a bug rather than something a caller can act on,
    and its text is the one place raw internals could reach a response body.
    """
    logger.error(
        "[manifest_apply] engine failure recorded, apply_id=%s: %s",
        report.apply_id,
        exc,
    )


def terminate_unstartable(
    applies: BotConfigManifestApplyRepositoryProtocol,
    locks: BotConfigManifestApplyLockRepositoryProtocol,
    payload: dict,
    exc: Exception,
) -> None:
    """Record a FAILED report and release the lock for an apply that cannot run.

    Both halves matter. Without the report the row polls ``RUNNING`` until
    its lock goes stale; without the release the bot is locked against every
    future apply for the whole TTL.
    """
    env = str(payload["env"])
    entity_id = str(payload["entity_id"])
    bot_id = str(payload["bot_id"])
    apply_id = str(payload["apply_id"])
    report = ApplyReport(
        apply_id=apply_id,
        bot_id=bot_id,
        trigger=str(payload["trigger"]),
        status=ApplyStatus.FAILED,
        started_at=parse_started_at(payload.get("started_at")),
        finished_at=datetime.now(),
        categories=(),
    )
    record_engine_failure(report, exc)
    try:
        applies.finish(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            apply_id=apply_id,
            status=report.status.value,
            report=json.dumps(report.as_payload()),
        )
    finally:
        locks.release(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            lock_token=str(payload["lock_token"]),
        )


def terminate_on_launch_failure(
    applies: BotConfigManifestApplyRepositoryProtocol,
    locks: BotConfigManifestApplyLockRepositoryProtocol,
    *,
    env: str,
    entity_id: str,
    bot_id: str,
    apply_id: str,
    trigger: str,
    started_at: datetime,
    lock_token: str,
    exc: BaseException,
) -> None:
    """A RUNNING report whose work never launched: finish it, free the bot.

    The mirrored ``finally`` of ``_run`` for the one path that cannot run
    it: a terminal ``FAILED`` record (the failure swims in the log, not
    the stored report, same rule as ``record_engine_failure``) written
    *before* the lock is released, so a poller never observes a
    lock-less RUNNING row and a re-apply never waits out the TTL for an
    apply that never started.
    """
    logger.error(
        "[manifest_apply] launch failed before the work could be "
        "handed off, apply_id=%s, bot_id=%s: %s",
        apply_id,
        bot_id,
        exc,
    )
    try:
        applies.finish(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            apply_id=apply_id,
            status=ApplyStatus.FAILED.value,
            report=json.dumps(
                ApplyReport(
                    apply_id=apply_id,
                    bot_id=bot_id,
                    trigger=trigger,
                    status=ApplyStatus.FAILED,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    categories=(),
                ).as_payload()
            ),
        )
    except Exception:
        # The lock release is the load-bearing half here; a record that
        # could not be terminated is a stranded-RUNNING row, which the
        # read-time abandonment derivation already answers for.
        logger.exception(
            "[manifest_apply] could not terminate a launch-failed report, "
            "apply_id=%s",
            apply_id,
        )
    finally:
        locks.release(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            lock_token=lock_token,
        )


__all__ = [
    "parse_started_at",
    "record_engine_failure",
    "terminate_on_launch_failure",
    "terminate_unstartable",
]
