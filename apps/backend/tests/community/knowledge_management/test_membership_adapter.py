"""Exact snapshot acknowledgements and gateway-only service admission."""

import httpx
import pytest
from agentclaw.community.adapters.knowledge_platform.space_knowledge import (
    EcbSpaceKnowledgeConfig,
)
from agentclaw.community.adapters.knowledge_platform.space_membership import (
    EcbSpaceMembershipSink,
)
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot


@pytest.fixture
def snapshot():
    return SpaceMembershipSnapshot(
        "pre",
        7,
        {
            "revision": 1,
            "full_snapshot": True,
            "space_type": "TEAM",
            "name": "Team",
            "creator_staff_no": "001234",
            "personal_owner_staff_no": None,
            "status": "active",
            "members": [{"staff_no": "001234", "role": "ADMIN"}],
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"tc_env": "prod"},
        {"tc_space_id": "8"},
        {"tc_revision": 2},
        {"tc_revision": True},
        {"tc_snapshot_hash": "wrong"},
        {"status": "disabled"},
    ],
)
def test_membership_requires_exact_ack_and_sends_no_bearer(snapshot, changes):
    calls = []

    def request(req):
        calls.append(req)
        assert "authorization" not in req.headers
        assert req.url.path == "/api/v1/knowledge/integrations/tc/spaces/7"
        return httpx.Response(
            200,
            json={
                "tc_env": "pre",
                "tc_space_id": "7",
                "tc_revision": 1,
                "tc_snapshot_hash": snapshot.digest(),
                "status": "active",
                **changes,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        sink = EcbSpaceMembershipSink(
            EcbSpaceKnowledgeConfig(
                enabled=True, base_url="https://ecb.test", tc_env="pre"
            ),
            client,
        )
        if changes:
            with pytest.raises(SpaceKnowledgeError, match="SPACE_NOT_SYNCED"):
                sink.apply_snapshot(snapshot)
        else:
            sink.apply_snapshot(snapshot)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "config",
    [
        {"enabled": False},
        {"enabled": True, "base_url": "https://ecb.test", "tc_env": "prod"},
    ],
)
def test_disabled_or_wrong_environment_never_calls_remote(snapshot, config):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as client:
        with pytest.raises(SpaceKnowledgeError):
            EcbSpaceMembershipSink(
                EcbSpaceKnowledgeConfig(**config), client
            ).apply_snapshot(snapshot)
