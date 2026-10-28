"""Adapt validated risk applications to existing publication services."""
import json

from agentclaw.community.core.operator_context import OperatorContext


class RiskPublishAdapter:
    def __init__(self, bots, public_service):
        self.bots, self.public_service = bots, public_service

    def __call__(self, row, on_started):
        bot = self.bots.get_by_pk(row.bot_pk)
        if not bot:
            raise LookupError("risk bot unavailable")
        target = json.loads(row.target_json)
        operator = OperatorContext(staff_id=row.operator_id, staff=row.operator_id)
        if row.public_scope == "legacy":
            risk_args = {}
            if target["permission_owner"] == "owner":
                risk_args = {"approval_biz_id": row.approval_biz_id, "risk_check_id": row.risk_check_id,
                             "on_approval_started": on_started}
            result = self.public_service.public_bot(
                bot_id=bot["bot_id"], owner_id=row.owner_id, public="1",
                permission_owner=target["permission_owner"], friend_approval=target["friend_approval"], operator=operator,
                **risk_args,
            )
            if target["permission_owner"] == "owner":
                if not isinstance(result, dict) or not (result.get("ext") or {}).get("public_approval", {}).get("puid"):
                    raise RuntimeError("legacy approval outcome unknown")
                return "approval"
            if not isinstance(result, dict) or str(result.get("public")) != "1":
                raise RuntimeError("legacy direct publication outcome unknown")
            return "published"
        if row.public_scope not in {"user", "agent"}:
            raise ValueError("unknown public scope")
        result = self.public_service.public_bcs_bot(
            bot_uid=f"{bot['bot_id']}:{bot['entity_id']}", owner_id=row.owner_id,
            public_scope=row.public_scope, operator=operator, view_depts=target["view_depts"],
            visibility=target["visibility"], approval_biz_id=row.approval_biz_id,
            on_approval_started=on_started, risk_check_id=row.risk_check_id,
        )
        if not result.get("success") or not result.get("puid"):
            raise RuntimeError("BCS approval outcome unknown")
        return "approval"
