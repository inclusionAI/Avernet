"""Handoff one committed Local Skill package to security observation."""

from __future__ import annotations

import asyncio
from threading import Event
from collections.abc import Callable
from uuid import uuid4

from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
)
from agentclaw.community.core.skill_center.services.local_skill_batch import PreparedLocalSkill
from agentclaw.community.log import get_logger

logger = get_logger()


async def handoff_local_skill_security(
    *, observer_provider: Callable[[], LocalSkillSecurityUploadHook],
    bot_id: str, owner_id: str, actor_id: str, skill: PreparedLocalSkill,
    skill_id: int, operation: str, handoff_timeout_seconds: float,
    record_timeout_seconds: float,
) -> None:
    """Preserve the frozen snapshot and cancellation fence of this upload."""
    validated = skill.package
    observation_id, digest = str(uuid4()), skill.digest
    request_cancelled = False
    handoff_cancelled = Event()
    try:
        observer = observer_provider()
        # Formal persistence has succeeded and the Bot edit lock is
        # still held. A failed identity bind skips observation only.
        incarnation_id = observer.current_incarnation_id(
            bot_id=bot_id, owner_id=owner_id,
            skill_id=skill_id,
        )
        if incarnation_id is None:
            raise ValueError("formal Local Skill security UUID is unavailable")
        handoff = asyncio.create_task(
            asyncio.to_thread(
                observer.observe,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=actor_id,
                skill_id=skill_id,
                operation=operation,
                name=validated.name,
                description=validated.description,
                canonical_zip=validated.canonical_zip,
                observation_id=observation_id,
                incarnation_id=incarnation_id,
                cancelled=handoff_cancelled,
            )
        )
        deadline = (
            asyncio.get_running_loop().time() + handoff_timeout_seconds
        )
        while (
            not handoff.done() and asyncio.get_running_loop().time() < deadline
        ):
            try:
                await asyncio.wait_for(
                    asyncio.shield(handoff),
                    timeout=max(
                        0.0, deadline - asyncio.get_running_loop().time()
                    ),
                )
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                request_cancelled = True
        if handoff.done():
            handoff.result()
        else:
            handoff_cancelled.set()
            logger.error(
                "Local Skill security handoff failed stage=TIMEOUT "
                "bot_id=%s owner_id=%s actor_id=%s skill_id=%s "
                "observation_id=%s operation=%s digest=%s",
                bot_id,
                owner_id,
                actor_id,
                skill_id,
                observation_id,
                operation,
                digest,
            )
            failure_record = asyncio.create_task(
                asyncio.to_thread(
                    observer.record_handoff_timeout,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_id=actor_id,
                    skill_id=skill_id,
                    observation_id=observation_id,
                    digest=digest,
                    incarnation_id=incarnation_id,
                    name=validated.name,
                    description=validated.description,
                    creator_id=actor_id if operation == "created" else None,
                )
            )
            failure_record.add_done_callback(_log_late_security_handoff)
            try:
                await asyncio.wait_for(
                    asyncio.shield(failure_record),
                    timeout=record_timeout_seconds,
                )
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                request_cancelled = True
            # Completion is deliberately detached. A late OSS result
            # cannot publish a current observation once cancelled.
            handoff.add_done_callback(_log_late_security_handoff)
    except Exception:
        logger.exception(
            "[LocalSkillUploadService] security observation handoff failed "
            "bot_id=%s owner_id=%s actor_id=%s skill_id=%s "
            "observation_id=%s operation=%s digest=%s",
            bot_id,
            owner_id,
            actor_id,
            skill_id,
            observation_id,
            operation,
            digest,
        )
    if request_cancelled:
        raise asyncio.CancelledError


def _log_late_security_handoff(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Local Skill late security handoff failed")
