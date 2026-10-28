"""Ordinary access-policy composition through the resource-content consumer."""

import pytest
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    ContentAccessPolicy,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_content import ContentAccess
from injector import InstanceProvider


def test_content_consumer_uses_request_scoped_session(world):
    class Session:
        def __init__(self):
            self.calls = []

        def __call__(self, actor_id, session):
            self.calls.append({"actor_id": actor_id, "session": session})
            return ContentAccess(actor_id, {"X-Test-Session": session["session"]})

    session = Session()
    world.injector.binder.bind(ContentAccessPolicy, to=InstanceProvider(session))
    consumer = world.get(KnowledgeContentServiceProtocol)
    access = consumer.prepare_access(actor_id="owner", session={"session": "fixture"})
    assert access.actor_id == "owner"
    assert dict(access.credentials) == {"X-Test-Session": "fixture"}
    assert session.calls == [{"actor_id": "owner", "session": {"session": "fixture"}}]


def test_content_consumer_preserves_session_denial(world):
    class Session:
        calls = []

        def __call__(self, actor_id, session):
            self.calls.append({"actor_id": actor_id, "session": session})
            raise KnowledgeError("KNOWLEDGE_IDENTITY_MISMATCH", 403)

    session = Session()
    world.injector.binder.bind(ContentAccessPolicy, to=InstanceProvider(session))
    consumer = world.get(KnowledgeContentServiceProtocol)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_IDENTITY_MISMATCH"):
        consumer.prepare_access(actor_id="owner", session={})
    assert session.calls == [{"actor_id": "owner", "session": {}}]


def test_unconfigured_session_never_returns_credentials(world):
    consumer = world.get(KnowledgeContentServiceProtocol)
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CONTENT_UNAVAILABLE"):
        consumer.prepare_access(actor_id="owner", session={"session": "fixture"})


@pytest.mark.asyncio
async def test_shared_async_transport_is_discovered_for_terminal_teardown(world):
    from agentclaw.community.adapters.knowledge_platform.content_ports import (
        AsyncContentTransport,
    )
    from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants

    transport = world.get(AsyncContentTransport)
    participants = discover_lifecycle_participants(world.injector)
    assert sum(item is transport for item in participants) == 1
    await transport.teardown()
    with pytest.raises(RuntimeError, match="shut down"):
        transport.build_request("GET", "https://knowledge.test/closed")
