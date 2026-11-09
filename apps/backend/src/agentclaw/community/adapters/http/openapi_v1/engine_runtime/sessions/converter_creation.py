"""Prefer OpenClaw creation metadata, with bounded legacy reconciliation."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas_helpers import _as_list
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.log import get_logger

logger = get_logger()

_LIMIT = 20
# Older Adapters may acknowledge creation before the list contains the session.
_DELAYS = (0.0, 0.5, 1.5)
_MANAGED_SESSION_KEY = re.compile(
    r"^(?:agent:([^:]+):)?(session:[^:]+:user:[^:]+)$",
    re.IGNORECASE,
)


def _matches(candidate_id: str, created_id: str) -> bool:
    if candidate_id == created_id:
        return True

    candidate_match = _MANAGED_SESSION_KEY.fullmatch(candidate_id)
    created_match = _MANAGED_SESSION_KEY.fullmatch(created_id)
    if candidate_match is None or created_match is None:
        return False

    candidate_agent, candidate_relative = candidate_match.groups()
    created_agent, created_relative = created_match.groups()
    if (candidate_agent is None) == (created_agent is None):
        return False
    return candidate_relative.lower() == created_relative.lower()


async def reconcile_created_session(
    *, relay: EngineRuntimeRelayProtocol, facts: BotFacts, bot_id: str,
    owner_id: str, user_id: str, stage: RuntimeStage,
    created_item: dict[str, Any], requested_title: str | None,
    device_uuid: str | None = None,
) -> dict[str, Any]:
    """Use creation metadata, or best-effort list recovery for older Adapters.

    Updated Adapters provide the canonical session ID and resolved model in
    ext_info.openclaw_creation. Only missing or incomplete metadata requires
    the legacy readback. A failed readback never retries the successful write.
    """
    if facts.active_engine.lower() != "openclaw":
        return created_item

    ext_info = created_item.get("ext_info")
    creation = ext_info.get("openclaw_creation") if isinstance(ext_info, dict) else None
    if isinstance(creation, dict):
        session_id = creation.get("session_id")
        model = creation.get("model")
        if (
            isinstance(session_id, str) and session_id.strip()
            and isinstance(model, str) and model.strip()
        ):
            item = {**created_item, "id": session_id, "model": model}
            if requested_title is not None:
                item["title"] = requested_title
            return item

    if isinstance(ext_info, dict) and "openclaw_creation" in ext_info:
        logger.warning(
            "[openapi.sessions.create] invalid creation metadata; using legacy reconciliation"
        )

    created_id = str(created_item.get("id") or "")
    if not created_id:
        return created_item

    for delay in _DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            listed = await relay.call(
                **({"device_uuid": device_uuid} if device_uuid is not None else {}),
                bot_id=bot_id, owner_id=owner_id, facts=facts,
                stage=stage.value, method="GET", path="/api/sessions",
                params={"offset": 0, "limit": _LIMIT, "user_id": user_id},
            )
            match = next((
                item for item in _as_list(listed.data)
                if _matches(str(item.get("id") or ""), created_id)
            ), None)
        except Exception as error:
            # Creation already succeeded. A failed optional read must not
            # invite a client retry that creates another empty session.
            logger.warning(
                "[openapi.sessions.create] canonical reconciliation failed: %s",
                type(error).__name__,
            )
            continue
        if match is None:
            continue

        reconciled = {**created_item, **match}
        if not match.get("model") and created_item.get("model"):
            reconciled["model"] = created_item["model"]
        for field in ("gmt_created", "gmt_modified"):
            if created_item.get(field):
                reconciled[field] = created_item[field]
        if requested_title is not None:
            reconciled["title"] = requested_title
        return reconciled

    return created_item
