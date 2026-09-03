"""OpenClaw create-response conversion for the public sessions surface."""
from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas_helpers import _as_list
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.log import get_logger

logger = get_logger()

# Direct Engine consumers keep OpenClaw's historical relative id. Only the
# public OpenAPI response is reconciled with its canonical list row.
_LIMIT = 20
# OpenClaw acknowledges ``sessions.patch`` before a newly-created empty
# session is necessarily visible through ``sessions.list``.  Cloud runtimes
# can take longer to publish that read model than an in-process/internal
# deployment, so keep the same three-attempt bound but allow up to two seconds
# for convergence.
_DELAYS = (0.0, 0.5, 1.5)


def _matches(candidate_id: str, created_id: str) -> bool:
    return candidate_id == created_id or candidate_id.endswith(f":{created_id}")


async def reconcile_created_session(
    *, relay: EngineRuntimeRelayProtocol, facts: BotFacts, bot_id: str,
    owner_id: str, user_id: str, stage: RuntimeStage,
    created_item: dict[str, Any], requested_title: str | None,
) -> dict[str, Any]:
    """Recover OpenClaw's canonical list row after a successful create."""
    if facts.active_engine.lower() != "openclaw":
        return created_item
    created_id = str(created_item.get("id") or "")
    if not created_id:
        return created_item

    for delay in _DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            listed = await relay.call(
                bot_id=bot_id, owner_id=owner_id, facts=facts,
                stage=stage.value, method="GET", path="/api/sessions",
                params={"offset": 0, "limit": _LIMIT, "user_id": user_id},
            )
        except Exception as error:
            # The write already succeeded. Do not invite a retry that creates
            # another empty session because this best-effort read failed. A
            # transient list failure must not consume all remaining attempts.
            logger.warning(
                "[openapi.sessions.create] canonical reconciliation failed: %s",
                type(error).__name__,
            )
            continue

        match = next((
            item for item in _as_list(listed.data)
            if _matches(str(item.get("id") or ""), created_id)
        ), None)
        if match is None:
            continue

        reconciled = {**created_item, **match}
        if not match.get("model") and created_item.get("model"):
            reconciled["model"] = created_item["model"]
        # The create row describes this operation's timestamps. Older
        # OpenClaw list adapters generated timestamps at read time.
        for field in ("gmt_created", "gmt_modified"):
            if created_item.get(field):
                reconciled[field] = created_item[field]
        if requested_title is not None:
            reconciled["title"] = requested_title
        return reconciled

    return created_item
