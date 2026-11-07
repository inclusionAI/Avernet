import json
from unittest.mock import MagicMock


from agentclaw.community.core.bot_public_risk.publish_adapter import RiskPublishAdapter
from agentclaw.community.core.bot_public_risk.repository import RiskCheck
from dataclasses import replace


def row(**kwargs):
    return replace(RiskCheck(risk_check_id="a", env="dev", bot_pk=1, owner_id="owner", operator_id="actor",
        client_scope="openapi:actor", client_request_id="request", public_scope="user", target_json="{}",
        request_hash="request", input_json="{}", input_hash="hash", approval_biz_id="biz-a",
        phase="ADVANCING", security_status="PASS", current_target_key="current"), **kwargs)


def test_bcs_publish_uses_stable_key_and_saved_operator():
    bots = MagicMock()
    bots.get_by_pk.return_value = {"bot_id": "b", "entity_id": "e"}
    public = MagicMock()
    public.public_bcs_bot.return_value = {"success": True, "puid": "p1"}
    adapter = RiskPublishAdapter(bots, public)
    record = row(target_json=json.dumps({"visibility": "protected", "view_depts": [{"deptNo": "1", "deptName": "d"}]}))
    on_started = MagicMock()
    assert adapter(record, on_started) == "approval"
    kwargs = public.public_bcs_bot.call_args.kwargs
    assert kwargs["bot_uid"] == "b:e"
    assert kwargs["operator"].staff_id == "actor"
    assert kwargs["approval_biz_id"] == record.approval_biz_id
    assert kwargs["risk_check_id"] == record.risk_check_id
    assert kwargs["on_approval_started"] is on_started


def test_legacy_owner_uses_risk_aware_approval_contract():
    bots = MagicMock()
    bots.get_by_pk.return_value = {"bot_id": "b", "entity_id": "e"}
    public = MagicMock()
    public.public_bot.return_value = {"ext": {"public_approval": {"puid": "p1"}}}
    record = row(public_scope="legacy", target_json=json.dumps({"permission_owner": "owner", "friend_approval": "0"}))
    handoff = MagicMock()
    assert RiskPublishAdapter(bots, public)(record, handoff) == "approval"
    kwargs = public.public_bot.call_args.kwargs
    assert kwargs["approval_biz_id"] == record.approval_biz_id
    assert kwargs["risk_check_id"] == record.risk_check_id
    assert kwargs["on_approval_started"] is handoff
