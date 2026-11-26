"""Keep the legacy Bot approval projection scoped to its approval instance."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, TYPE_CHECKING

from agentclaw.community.core.bot_public_risk.eligibility import BLOCK_NOTICE
if TYPE_CHECKING:
    from agentclaw.community.core.bot_public_risk.repository_protocol import RiskCheck
    from agentclaw.community.core.repository.protocols.bot import BotRepository


def archive_legacy_approval(ext: dict[str, Any], *, expected_puid: str | None = None) -> bool:
    approval = ext.get("public_approval")
    if not isinstance(approval, dict) or (expected_puid is not None and approval.get("puid") != expected_puid):
        return False
    old_approval = dict(ext.pop("public_approval"))
    if old_approval.get("status") == "PROCESSING":
        old_approval["status"] = "REPLACED"
    old_approval.setdefault("processed_at", datetime.now().isoformat())
    ext["public_approval_history"] = [*ext.get("public_approval_history", []), old_approval][-5:]
    return True


def project_legacy_approval_block(bots: BotRepository, row: RiskCheck) -> None:
    if row.public_scope != "legacy" or not row.approval_puid:
        return
    workflow = json.loads(row.workflow_json)
    reason = workflow.get("error")
    puid = row.approval_puid
    if (reason not in BLOCK_NOTICE or workflow.get("approval_decision") != "AGREE"
            or workflow.get("approval_decision_puid") != puid):
        return
    for _ in range(3):
        bot = bots.get_by_pk(row.bot_pk)
        if not bot:
            return
        ext = bot.get("ext") or {}
        approval = ext.get("public_approval") or {}
        if approval.get("puid") != puid or approval.get("status") != "PROCESSING":
            return
        blocked = {
            **approval,
            "status": "BLOCKED",
            "approval_decision": "AGREE",
            "block_reason": reason,
            "processed_at": datetime.now().isoformat(),
        }
        if bots.compare_and_set_ext(
            bot_id=bot["bot_id"], owner_id=bot["owner_id"], expected_ext=ext,
            ext={**ext, "public_approval": blocked},
        ) is not None:
            return
    raise RuntimeError("could not record blocked legacy public approval")
