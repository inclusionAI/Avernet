"""Recheck risk inputs and the original applicant's current permission."""

import json
from dataclasses import dataclass
from typing import Callable, Literal

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import resolve_explicit_permission_level
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskCheck
from agentclaw.community.log import get_logger

logger = get_logger()
EligibilityReason = Literal["CONFIG_CHANGED", "PERMISSION_CHANGED", "INPUT_OR_PERMISSION_INVALID"]
BLOCK_NOTICE = {
    "CONFIG_CHANGED": "CONFIG_CHANGED",
    "PERMISSION_CHANGED": "PERMISSION_CHANGED",
    "INPUT_OR_PERMISSION_INVALID": "INPUT_INVALID",
}


@dataclass(frozen=True)
class EligibilityResult:
    ok: bool
    reason: EligibilityReason | None = None
    diff: dict[str, dict[str, int]] | None = None


EligibilityCheck = Callable[[RiskCheck], EligibilityResult | bool]


def evaluate_eligibility(check: EligibilityCheck, row: RiskCheck) -> EligibilityResult:
    result = check(row)
    if isinstance(result, EligibilityResult):
        return result
    if result is True:
        return EligibilityResult(ok=True)
    return EligibilityResult(ok=False, reason="INPUT_OR_PERMISSION_INVALID")


def log_block(row: RiskCheck, result: EligibilityResult, *, gate: str, puid: str | None = None) -> None:
    logger.info(
        "bot_public_risk_eligibility_block gate=%s risk_check_id=%s bot_pk=%s puid=%s reason=%s resource_diff=%s",
        gate, row.risk_check_id, row.bot_pk, puid, result.reason, result.diff,
    )


def _resource_diff(previous: dict, current: dict) -> dict[str, dict[str, int]]:
    def by_identity(payload):
        return {
            (resource["resourceType"], resource["resourcePlatform"], resource["resourceCode"]): resource
            for resource in payload.get("agent_info", {}).get("resourceManifest", [])
        }

    old, new = by_identity(previous), by_identity(current)
    added, removed = new.keys() - old.keys(), old.keys() - new.keys()
    changed = {key for key in old.keys() & new.keys() if old[key] != new[key]}
    return {
        "total": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        "mcp": {
            "added": sum(key[0] == "MCP" for key in added),
            "removed": sum(key[0] == "MCP" for key in removed),
            "changed": sum(key[0] == "MCP" for key in changed),
        },
    }


class RiskEligibility:
    def __init__(self, bots, collaborators, inputs):
        self.bots, self.collaborators, self.inputs = bots, collaborators, inputs

    def __call__(self, row: RiskCheck) -> bool:
        return self.evaluate(row).ok

    def evaluate(self, row: RiskCheck) -> EligibilityResult:
        bot = self.bots.get_by_pk(row.bot_pk)
        if not bot or str(bot.get("owner_id")) != row.owner_id or str(bot.get("env")) != row.env:
            return EligibilityResult(ok=False, reason="PERMISSION_CHANGED")
        level = resolve_explicit_permission_level(
            self.collaborators, bot=bot, user_id=row.operator_id, owner_id=row.owner_id, env=row.env
        )
        if level < PermissionLevel.ADMIN:
            return EligibilityResult(ok=False, reason="PERMISSION_CHANGED")
        current, digest = self.inputs.build(bot=bot, target=json.loads(row.target_json))
        if digest == row.input_hash:
            return EligibilityResult(ok=True)
        return EligibilityResult(
            ok=False,
            reason="CONFIG_CHANGED",
            diff=_resource_diff(json.loads(row.input_json), current),
        )
