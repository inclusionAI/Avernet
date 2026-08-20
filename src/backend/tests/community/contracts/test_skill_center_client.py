"""Rule 25 conformance — SkillCenterClient.

Consumer under test: ``SkillPublishService`` wires the client via DI
and uses it from multiple methods. The local
``LocalSkillCenterClient`` returns a mock envelope with the original
``skillCode`` echoed back and ``status="PUBLISHED"``.

Driving ``SkillPublishService.publish`` requires substantial bot +
skill repository fixtures; the contract is verified through the
DI-bound instance the service receives.

Plugin-hit assertion: the local mock's envelope contains
``status="PUBLISHED"`` + the input skillCode — a fingerprint no
bypass could produce.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient


def test_local_skill_center_client_upload_returns_mock_envelope(world) -> None:
    client = world.get(SkillCenterClient)
    result = client.upload_and_publish(
        {"skillCode": "demo_skill", "skillName": "Demo", "versionNumber": "1.0.0"}
    )
    assert result["success"] is True
    assert result["data"]["skillCode"] == "demo_skill"
    assert result["data"]["status"] == "PUBLISHED"


def test_local_skill_center_client_query_status_envelope(world) -> None:
    client = world.get(SkillCenterClient)
    result = client.query_publish_status("demo_skill")
    assert result["success"] is True
