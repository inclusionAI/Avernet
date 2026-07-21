from __future__ import annotations

from agentclaw.community.core.session_resources.materialization import (
    SessionResourceMaterializeHandler,
)
from agentclaw.community.core.task_queue.types import Complete, Retry


class _Resolver:
    def resolve_for_bot(self, bot_id, owner_id):
        return type(
            "Context",
            (),
            {"conn_info": {"target": "engine"}, "provider": "baas"},
        )()


class _Transport:
    def __init__(self, accepted=True) -> None:
        self.accepted = accepted
        self.calls = []

    async def invoke(self, conn_info, method, path, body=None, params=None):
        self.calls.append((conn_info, method, path, body))
        return {"accepted": self.accepted}


def _payload():
    return {
        "resource_id": "sr_001",
        "transfer_id": "transfer-1",
        "task_id": "task-1",
        "task_version": 1,
        "scope_key_hash": "scope-hash",
        "session_key_hash": "session-hash",
        "device_path": "workspace/.teamclaw/session-files/scope-hash/session-hash/sr_001/a.txt",
        "filename": "a.txt",
        "size_bytes": 1,
        "content_hash": "hash",
        "owner_id": "owner-1",
        "bot_id": "bot-1",
    }


def test_handler_dispatches_to_shared_engine_endpoint():
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(_Resolver(), transport)

    result = handler.handle(_payload())

    assert isinstance(result, Complete)
    assert transport.calls[0][1:3] == ("POST", "/api/resource-materializations")
    assert transport.calls[0][3]["task_version"] == 1
    assert "owner_id" not in transport.calls[0][3]


def test_handler_retries_when_engine_does_not_accept():
    handler = SessionResourceMaterializeHandler(_Resolver(), _Transport(False))

    result = handler.handle(_payload())

    assert isinstance(result, Retry)
