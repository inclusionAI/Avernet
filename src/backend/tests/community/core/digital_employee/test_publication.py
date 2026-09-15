import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError, DigitalEmployeeSettings
from agentclaw.community.core.digital_employee.events import APPROVAL_RESULT
from agentclaw.community.core.digital_employee.publication import DigitalEmployeePublicationService
from agentclaw.community.core.digital_employee.snapshot import capability_digest


@pytest.fixture
def s():
    bot = {"id": 17, "bot_id": "bot-17", "owner_id": "owner", "ext": {"digital_employee": {
        "status": "ACTIVE", "work_no": "AI00000124", "agent_id": "17"}}}
    snapshot = {"agent_code": "agent-17", "package_hashes": {}, "capabilities": {
        "skills": [], "mcps": [{"mcpServerCode": "keep", "identityMode": "CALLER"}], "clis": []}}
    record = SimpleNamespace(id=42, source_bot_pk=17, last_pub_id=None, version=2,
                             status="validating", ext={"digital_employee_snapshot": snapshot})
    bots, records, reader, platform, passport, history = [Mock() for _ in range(6)]
    reader.read_published.side_effect = lambda _bot, pub: copy.deepcopy(pub.ext["digital_employee_snapshot"])
    bots.get_bot.return_value = bot
    records.get_by_id.return_value = record

    def save(*, publish_id, expected_ext, ext):
        assert publish_id == record.id and expected_ext == record.ext
        record.ext = copy.deepcopy(ext)
        return record

    records.compare_and_set_ext.side_effect = save
    target = {"platformCode": "platform", "agentId": "17", "agentCode": "agent-17", "employeeWorkNo": "AI00000124"}
    platform.create_skill_change.return_value = {"taskId": "task-42", "target": target}
    platform.query_skill_change.return_value = {"taskId": "task-42", "target": target, "status": "APPROVED", "security": {"isPassed": True}}
    service = DigitalEmployeePublicationService(DigitalEmployeeSettings(True, "platform"), bots, records, reader, platform, passport, history)
    return SimpleNamespace(**locals())


def event(s, status="APPROVED"):
    return {"specversion": "1.0", "id": "3e5c47aa-ecc8-4972-8123-f70a9a11ef57",
            "source": "urn:ant:aiworkmng", "type": APPROVAL_RESULT,
            "subject": "digital-employee/AI00000124", "time": "2026-08-25T02:30:45Z",
            "datacontenttype": "application/json", "data": {
                "workNo": "AI00000124", "taskId": "task-42", "platformCode": "platform",
                "agentId": "17", "agentCode": "agent-17",
                "requestId": s.record.ext["digital_employee_approval"]["request_id"],
                "approvalStatus": status, "operate": "agree" if status == "APPROVED" else "reject"}}


def test_unbound_unchanged(s):
    s.bot["ext"] = {}
    assert s.service.prepare_online(42, "owner")
    s.service.require_approved(42)
    s.service.finalize_scope(42)
    s.platform.create_skill_change.assert_not_called()
    s.passport.update_passport.assert_not_called()


def test_wait_then_finalize_exact_scope(s):
    assert not s.service.prepare_online(42, "owner")
    assert not s.service.prepare_online(42, "owner")
    s.platform.create_skill_change.assert_called_once()
    with pytest.raises(DigitalEmployeeError):
        s.service.require_approved(42)
    assert s.service.handle_approval_result(event(s)) == (42, "owner")
    assert s.service.prepare_online(42, "owner")
    s.service.finalize_scope(42)
    scope = s.passport.update_passport.call_args.kwargs["resource_scope"]
    assert scope["mcp_codes"] == ["keep"]
    assert scope["mcp_items"][0]["identity_mode"] == "caller"
    assert scope["skill_items"] == []


@pytest.mark.parametrize("field,value", [("taskId", "other"), ("agentId", "18"), ("agentCode", "other")])
def test_wrong_target_cannot_publish(s, field, value):
    s.service.prepare_online(42, "owner")
    message = event(s)
    message["data"][field] = value
    with pytest.raises(DigitalEmployeeError):
        s.service.handle_approval_result(message)
    assert s.record.ext["digital_employee_approval"]["status"] == "APPROVING"


def test_security_failure_does_not_approve(s):
    s.service.prepare_online(42, "owner")
    s.platform.query_skill_change.return_value["security"]["isPassed"] = False
    with pytest.raises(DigitalEmployeeError):
        s.service.handle_approval_result(event(s))
    assert s.record.ext["digital_employee_approval"]["status"] == "APPROVING"


def test_rejection_terminal(s):
    s.service.prepare_online(42, "owner")
    assert s.service.handle_approval_result(event(s, "REJECTED")) is None
    assert s.service.handle_approval_result(event(s)) is None
    with pytest.raises(DigitalEmployeeError):
        s.service.prepare_online(42, "owner")


def test_changed_snapshot_invalidates_approval(s):
    s.service.prepare_online(42, "owner")
    s.service.handle_approval_result(event(s))
    s.record.ext["digital_employee_snapshot"]["capabilities"]["mcps"] = []
    with pytest.raises(DigitalEmployeeError):
        s.service.finalize_scope(42)
    s.passport.update_passport.assert_not_called()


def test_no_fake_empty_baseline(s):
    s.record.last_pub_id = 41
    s.records.get_by_id.side_effect = lambda key: s.record if key == 42 else SimpleNamespace(ext={})
    s.history.read.side_effect = DigitalEmployeeError("原发布产物不可读")
    with pytest.raises(DigitalEmployeeError, match="原发布产物"):
        s.service.prepare_online(42, "owner")
    s.platform.create_skill_change.assert_not_called()


def test_timeout_reuses_request_id(s):
    result = s.platform.create_skill_change.return_value
    s.platform.create_skill_change.side_effect = [RuntimeError("timeout"), result]
    with pytest.raises(RuntimeError):
        s.service.prepare_online(42, "owner")
    key = s.record.ext["digital_employee_approval"]["request_id"]
    assert not s.service.prepare_online(42, "owner")
    assert all(call.args[1] == key for call in s.platform.create_skill_change.call_args_list)


def test_hash_includes_content_not_url():
    before = {"package_hashes": {"skill": "a" * 64}, "capabilities": {"skills": [{"ossAddress": "https://example.org/a"}]}}
    after = copy.deepcopy(before)
    after["capabilities"]["skills"][0]["ossAddress"] = "https://example.org/b"
    assert capability_digest(before) == capability_digest(after)
    after["package_hashes"]["skill"] = "b" * 64
    assert capability_digest(before) != capability_digest(after)


def test_old_publication_uses_real_artifact_baseline(s):
    s.record.last_pub_id = 41
    previous = SimpleNamespace(ext={}, version=1)
    s.records.get_by_id.side_effect = lambda key: s.record if key == 42 else previous
    before = {"skills": [{"name": "old-skill"}], "mcps": [{"mcpServerCode": "old-mcp"}], "clis": []}
    s.history.read.return_value = before
    assert not s.service.prepare_online(42, "owner")
    s.history.read.assert_called_once_with(s.bot, previous)
    assert s.platform.create_skill_change.call_args.args[2]["before"] == before
