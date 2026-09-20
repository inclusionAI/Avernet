from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError, DigitalEmployeeSettings
from agentclaw.community.core.digital_employee.service import DigitalEmployeeService
from agentclaw.community.core.digital_employee.events import ONBOARDED


@pytest.fixture
def scenario():
    bot = {"id": 17, "bot_id": "service-17", "owner_id": "owner", "creator_id": "owner", "entity_id": "owner",
           "bot_type": "service", "binding_id": 7, "env": "dev", "ext": {}}
    repo, platform, identities, devices, publications, identity_bindings, mcps, passport = [Mock() for _ in range(8)]
    repo.get_bot.return_value = bot
    repo.list_service_bots.return_value = [bot]
    repo.begin_binding.return_value = {"phase": "DETAIL_READY"}
    repo.change_binding_phase.return_value = True
    platform.get_employee.return_value = {"workNo": "AI00000124", "agentId": "17",
                                          "platformCode": "platform", "status": "APPROVED"}
    identities.change_execution_identity.return_value = {"status": "ACTIVE", "token_injected": True}
    devices.get_by_id.return_value = SimpleNamespace(status="ACTIVE")
    publications.list_by_source_bot.return_value = []
    identity_bindings.get_active.return_value = None
    passport.query_agent_passport.return_value = {"agent_code": "code-17"}
    service = DigitalEmployeeService(DigitalEmployeeSettings(True, "platform", ""), repo,
                                     platform, identities, devices, publications,
                                     identity_bindings, mcps, passport)
    return SimpleNamespace(**locals())


def message():
    return {"specversion": "1.0", "id": "3e5c47aa-ecc8-4972-8123-f70a9a11ef57",
            "source": "urn:ant:aiworkmng", "type": ONBOARDED,
            "subject": "digital-employee/AI00000124", "time": "2026-08-25T02:30:45Z",
            "datacontenttype": "application/json", "data": {"workNo": "AI00000124"}}


def test_draft_without_publication_can_bind(scenario):
    s = scenario
    assert s.service.list_bindable("owner")[0]["agentId"] == "17"
    s.service.handle_onboarded(message())
    s.identities.change_execution_identity.assert_called_once_with(
        bot_id="service-17", owner_id="owner", modifier_id="owner", action="reissue",
        execution_workno="AI00000124", identity_type="DIGITAL_EMPLOYEE",
    )
    s.repo.change_binding_phase.assert_called_with(17, "AI00000124", "REISSUING", "ACTIVE")


def test_online_only_instance_can_bind(scenario):
    s = scenario
    s.bot["binding_id"] = None
    s.publications.list_by_source_bot.return_value = [SimpleNamespace(status="success", ext={"binding": {"online": 8}})]
    assert len(s.service.list_bindable("owner")) == 1
    s.devices.get_by_id.assert_called_with(8)


@pytest.mark.parametrize("failure", ["personal", "no_runtime", "detail_mismatch"])
def test_invalid_binding_never_reissues(scenario, failure):
    s = scenario
    if failure == "personal":
        s.bot["bot_type"] = "personal"
    elif failure == "no_runtime":
        s.devices.get_by_id.return_value = None
    else:
        s.platform.get_employee.return_value["workNo"] = "wrong"
    with pytest.raises(DigitalEmployeeError):
        s.service.handle_onboarded(message())
    s.identities.change_execution_identity.assert_not_called()


def test_injection_failure_does_not_mark_bound(scenario):
    s = scenario
    s.identities.change_execution_identity.side_effect = RuntimeError("runtime unavailable")
    with pytest.raises(RuntimeError):
        s.service.handle_onboarded(message())
    s.repo.change_binding_phase.assert_called_once_with(17, "AI00000124", "DETAIL_READY", "REISSUING")


def test_retry_reconciles_instead_of_reissuing(scenario):
    s = scenario
    s.repo.change_binding_phase.side_effect = [False, True]
    s.service.handle_onboarded(message())
    assert s.identities.change_execution_identity.call_args.kwargs["action"] == "activate"


def test_retry_recovers_crash_before_pending_identity_created(scenario):
    s = scenario
    s.repo.change_binding_phase.side_effect = [False, True]
    s.identity_bindings.get_pending.return_value = None
    s.service.handle_onboarded(message())
    assert s.identities.change_execution_identity.call_args.kwargs["action"] == "reissue"
    s.repo.change_binding_phase.assert_called_with(17, "AI00000124", "REISSUING", "ACTIVE")


def test_retry_recovers_identity_commit_before_metadata_commit(scenario):
    s = scenario
    s.repo.change_binding_phase.side_effect = [False, True]
    s.identity_bindings.get_active.return_value = SimpleNamespace(execution_workno="AI00000124", identity_type="DIGITAL_EMPLOYEE")
    s.service.handle_onboarded(message())
    s.identities.change_execution_identity.assert_not_called()
    s.repo.change_binding_phase.assert_called_with(17, "AI00000124", "REISSUING", "ACTIVE")


def test_completed_duplicate_is_side_effect_free(scenario):
    s = scenario
    s.bot["ext"]["digital_employee"] = {"work_no": "AI00000124", "phase": "ACTIVE"}
    s.service.handle_onboarded(message())
    s.repo.begin_binding.assert_not_called()
    s.identities.change_execution_identity.assert_not_called()


def test_unbound_mcp_change_preserves_legacy_removal(scenario):
    s = scenario
    assert s.service.prepare_mcp_change(s.bot, [], [{"mcp_code": "old"}], "owner") == []
    s.platform.apply_mcp_permissions.assert_not_called()


def test_bound_public_mcp_does_not_apply_and_preserves_online_grant(scenario):
    s = scenario
    s.bot["ext"]["digital_employee"] = {"status": "ACTIVE", "work_no": "AI00000124", "agent_id": "17"}
    s.mcps.get_mcp_detail.return_value = {"accessLevel": "PUBLIC"}
    scope = s.service.prepare_mcp_change(s.bot, [{"mcp_code": "new"}], [{"mcp_code": "old"}], "owner")
    assert {item["mcp_code"] for item in scope} == {"new", "old"}
    s.platform.apply_mcp_permissions.assert_not_called()


def test_private_mcp_uses_employee_and_stable_request_id(scenario):
    s = scenario
    s.bot["ext"]["digital_employee"] = {"status": "ACTIVE", "work_no": "AI00000124", "agent_id": "17"}
    s.mcps.get_mcp_detail.return_value = {"accessLevel": "PRIVATE"}
    s.platform.apply_mcp_permissions.return_value = {"success": True}
    for _ in range(2):
        s.service.prepare_mcp_change(s.bot, [{"mcp_code": "private"}], [], "operator")
    first, second = s.platform.apply_mcp_permissions.call_args_list
    assert first == second
    assert first.args[1]["workerId"] == "AI00000124"
    assert first.args[1]["agentId"] == "17"


def test_partial_mcp_application_is_not_success(scenario):
    s = scenario
    s.bot["ext"]["digital_employee"] = {"status": "ACTIVE", "work_no": "AI00000124", "agent_id": "17"}
    s.mcps.get_mcp_detail.return_value = {"accessLevel": "PRIVATE"}
    s.platform.apply_mcp_permissions.return_value = {"success": False, "successCount": 1, "failCount": 1}
    with pytest.raises(DigitalEmployeeError, match="部分"):
        s.service.prepare_mcp_change(s.bot, [{"mcp_code": "private"}], [], "owner")
