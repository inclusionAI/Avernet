from dataclasses import replace
from unittest.mock import MagicMock

from agentclaw.community.core.bot_public_risk.eligibility import RiskEligibility
from agentclaw.community.core.bot_public_risk.repository import RiskCheck


def row():
    return RiskCheck(risk_check_id="a", env="dev", bot_pk=42, owner_id="owner", operator_id="owner",
                     client_scope="legacy:owner", client_request_id="r", public_scope="legacy", target_json='{}',
                     request_hash="r", input_json="{}", input_hash="current", approval_biz_id="risk-a")


def test_h2_rebuilds_input_and_rejects_owner_or_digest_changes():
    bots = MagicMock()
    bots.get_by_pk.return_value = {"id": 42, "bot_id": "b", "entity_id": "e", "owner_id": "owner", "env": "dev"}
    builder = MagicMock()
    builder.build.return_value = ({"agent_info": {}}, "current")
    gate = RiskEligibility(bots, MagicMock(), builder)
    assert gate(row()) is True
    builder.build.return_value = ({"agent_info": {}}, "different")
    assert gate(row()) is False
    bots.get_by_pk.return_value["owner_id"] = "new-owner"
    builder.build.return_value = ({"agent_info": {}}, "current")
    assert gate(row()) is False


def test_h2_rechecks_original_applicant_explicit_permission():
    class Collaborators:
        def get_explicit_permission_level(self, **_):
            return 0

    bots = MagicMock()
    bots.get_by_pk.return_value = {"id": 42, "bot_id": "b", "entity_id": "e", "owner_id": "owner", "env": "dev"}
    inputs = MagicMock()
    gate = RiskEligibility(bots, Collaborators(), inputs)
    assert gate(replace(row(), operator_id="former-admin")) is False
    inputs.build.assert_not_called()
