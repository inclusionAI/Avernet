"""Submit a durable risk application and refresh its original external check."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol
from uuid import uuid4

from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RequestConflict, RiskCheck
from agentclaw.community.core.bot_public_risk.tasks import RiskTasks


class RiskInputBuilder(Protocol):
    """Construct fresh input and a stable digest from current Backend metadata."""
    def build(self, *, bot: Mapping[str, Any], target: dict[str, Any]) -> tuple[dict[str, Any], str]: ...


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _target(surface: str, body: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if surface == "legacy":
        if body.get("public") != "1" or body.get("permission_owner") not in {"caller", "owner"} or body.get("friend_approval") not in {"0", "1"}:
            raise ValueError("invalid legacy publication target")
        target = {"public_scope": "legacy", "visibility": "AUTHORIZED" if body["friend_approval"] == "1" else "PUBLIC", "permission_owner": body["permission_owner"], "friend_approval": body["friend_approval"], "view_depts": []}
        return target, target
    if surface != "openapi" or body.get("public_scope") not in {"user", "agent"} or body.get("visibility") not in {"public", "protected"}:
        raise ValueError("invalid publication target")
    departments = body.get("view_depts") or []
    if not isinstance(departments, list) or (body["visibility"] == "protected") != bool(departments):
        raise ValueError("invalid publication departments")
    for dept in departments:
        if not isinstance(dept, dict) or not dept.get("deptNo") or not dept.get("deptName"):
            raise ValueError("invalid department")
    target = {"public_scope": body["public_scope"], "visibility": body["visibility"], "view_depts": sorted(departments, key=lambda dept: dept["deptNo"])}
    canonical = {**target, "view_depts": sorted({dept["deptNo"] for dept in departments})}
    return target, canonical


class RiskApplication:
    """Application methods expect the caller to have authorized the resolved Bot."""

    def __init__(self, repository: BotPublicRiskRepository, tasks: RiskTasks, inputs: RiskInputBuilder) -> None:
        self._repository, self._tasks, self._inputs = repository, tasks, inputs

    def submit(self, *, bot: ResolvedBotContext, actor_id: str, surface: str, body: Mapping[str, Any]) -> tuple[RiskCheck, bool]:
        if not actor_id or actor_id == "anonymous":
            raise ValueError("authenticated actor is required")
        request_id = body.get("client_request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("invalid request identifier")
        target, canonical = _target(surface, body)
        scope = f"{surface}:{actor_id}"
        if len(scope) > 128:
            raise ValueError("client scope exceeds storage limit")
        env = str(bot.bot["env"])
        request_hash = sha256(_encode({"bot_pk": bot.bot_pk, "target": canonical}).encode()).hexdigest()
        previous = self._repository.get_by_request(env=env, client_scope=scope, client_request_id=request_id)
        if previous is not None:
            if previous.bot_pk != bot.bot_pk or previous.public_scope != target["public_scope"] or previous.request_hash != request_hash:
                raise RequestConflict("request ID reused with different intent")
            return previous, False
        input_value, input_hash = self._inputs.build(bot=bot.bot, target=target)
        if not isinstance(input_value, dict) or not isinstance(input_value.get("agent_info"), dict) or not isinstance(input_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", input_hash):
            raise ValueError("risk input or its digest is invalid")
        risk_id = str(uuid4())
        record = RiskCheck(risk_check_id=risk_id, env=env, bot_pk=bot.bot_pk, owner_id=bot.current_owner_id,
                           operator_id=actor_id, client_scope=scope, client_request_id=request_id,
                           public_scope=target["public_scope"], target_json=_encode(target), request_hash=request_hash,
                           input_json=_encode(input_value), input_hash=input_hash, approval_biz_id=f"risk-{risk_id}")
        return self._tasks.start(record)

    def refresh(self, *, bot: ResolvedBotContext, actor_id: str, record: RiskCheck) -> tuple[str, RiskCheck]:
        """Poll the current record's original check; never create a new external check."""
        env = str(bot.bot["env"])
        row = self._repository.get(env=env, risk_check_id=record.risk_check_id)
        if row is None or row.bot_pk != bot.bot_pk or row.env != env:
            raise LookupError("Risk application unavailable")
        if row.owner_id != bot.current_owner_id or row.current_target_key is None:
            return "NOT_QUERIED", row
        outcome = self._tasks.poll(env=env, risk_check_id=row.risk_check_id, manual=True)
        current = self._repository.get(env=env, risk_check_id=row.risk_check_id)
        if current is None or current.bot_pk != bot.bot_pk:
            raise LookupError("Risk application unavailable")
        return outcome, current
