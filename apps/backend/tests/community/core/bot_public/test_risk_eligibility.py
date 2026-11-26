import json
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
    assert gate.evaluate(row()).reason == "CONFIG_CHANGED"
    bots.get_by_pk.return_value["owner_id"] = "new-owner"
    builder.build.return_value = ({"agent_info": {}}, "current")
    assert gate.evaluate(row()).reason == "PERMISSION_CHANGED"


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


def test_configuration_result_counts_resource_changes_and_mcp_subset():
    def input_with(*resources):
        return {"agent_info": {"resourceManifest": list(resources)}}

    old_mcp = {"resourceType": "MCP", "resourcePlatform": "MCP", "resourceCode": "retained", "resourceVersion": "1"}
    removed_skill = {"resourceType": "SKILL", "resourcePlatform": "center", "resourceCode": "removed"}
    added_mcp = {**old_mcp, "resourceCode": "added"}
    old_input = input_with(old_mcp, removed_skill)
    new_input = input_with({**old_mcp, "resourceVersion": "2"}, added_mcp)
    bots = MagicMock()
    bots.get_by_pk.return_value = {"id": 42, "bot_id": "b", "entity_id": "e", "owner_id": "owner", "env": "dev"}
    inputs = MagicMock()
    inputs.build.return_value = (new_input, "different")
    gate = RiskEligibility(bots, MagicMock(), inputs)
    result = gate.evaluate(replace(row(), input_json=json.dumps(old_input)))
    assert result.reason == "CONFIG_CHANGED"
    assert result.diff == {
        "total": {"added": 1, "removed": 1, "changed": 1},
        "mcp": {"added": 1, "removed": 0, "changed": 1},
    }
