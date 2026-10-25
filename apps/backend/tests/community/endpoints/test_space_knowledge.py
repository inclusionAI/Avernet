"""Registered HTTP cases with real membership, orchestration and outbox writes.

Only external provider ports are replaced; the shared runner invokes each route
on the full application. These cases do not claim remote provisioning coverage.
"""

from dataclasses import asdict

from agentclaw.community.api.space_knowledge_service import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    KnowledgeBaseState,
    SpaceKnowledgeProvider,
    SpaceKnowledgeSync,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_ACTOR = "001234"
_PATH = "/api/spaces/{space_id}/knowledge-bases"
_STATE = {
    "kb_id": "kb-endpoint",
    "tenant_id": "tenant-endpoint",
    "name": "Endpoint knowledge",
    "description": "Endpoint contract",
    "status": "creating",
    "created_by": _ACTOR,
    "owner_id": _ACTOR,
}


class _AcceptingSink:
    def __init__(self):
        self.snapshots = []

    def is_enabled(self):
        return True

    def apply_snapshot(self, snapshot):
        self.snapshots.append(snapshot)


class _Provider:
    def __init__(self):
        self.calls = []
        self.state = None

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        self.state = KnowledgeBaseState(
            "kb-endpoint",
            "tenant-endpoint",
            kwargs["name"],
            kwargs["description"],
            "creating",
            kwargs["actor_id"],
            kwargs["actor_id"],
        )
        return KnowledgeBaseResult(self.state, True)

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        assert self.state is not None and kwargs["kb_id"] == self.state.kb_id
        return KnowledgeBaseResult(self.state)

    def list_knowledge_bases(self, **kwargs):
        self.calls.append(("list", kwargs))
        assert self.state is not None
        return KnowledgeBasePage([self.state])


def _seed_space(world):
    world.get(SpaceServiceProtocol).create_team(
        name="Knowledge team", creator_id=_ACTOR
    )
    sink = _AcceptingSink()
    sync = SpaceMembershipSynchronizer(
        world.get(SpaceKnowledgeSyncRepositoryProtocol),
        sink,
        "agentclaw",
        world.get(TaskQueueRepositoryProtocol),
    )
    world.injector.binder.bind(SpaceKnowledgeSync, to=sync)
    world.injector.binder.bind(SpaceKnowledgeProvider, to=_Provider())


def _seed_knowledge(world):
    _seed_space(world)
    world.get(SpaceKnowledgeServiceProtocol).create(
        space_id=1,
        actor_id=_ACTOR,
        request_id="seed-intent",
        name=_STATE["name"],
        description=_STATE["description"],
    )
    world.get(SpaceKnowledgeProvider).calls.clear()


def _assert_create(response, world):
    provider = world.get(SpaceKnowledgeProvider)
    operation, call = provider.calls[0]
    assert len(provider.calls) == 1 and operation == "create"
    assert call["actor_id"] == _ACTOR
    assert call["request_id"] == "endpoint-intent"
    sync = world.get(SpaceKnowledgeSync)
    assert sync.progress(space_id=1, environment="dev") == (1, 1, "active")
    assert sync._sink.snapshots[0].body["members"] == [
        {"staff_no": _ACTOR, "role": "ADMIN"}
    ]
    assert response.json() == asdict(provider.state)


def _assert_get(_response, world):
    operation, call = world.get(SpaceKnowledgeProvider).calls[0]
    assert operation == "get" and call["actor_id"] == _ACTOR
    assert call["kb_id"] == "kb-endpoint"


def _assert_list(_response, world):
    operation, call = world.get(SpaceKnowledgeProvider).calls[0]
    assert operation == "list" and call["actor_id"] == _ACTOR
    assert call["page_size"] == 2 and call["cursor"] == "kb-before"


def _assert_no_provider_call(_response, world):
    assert world.get(SpaceKnowledgeProvider).calls == []


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="member_creates_after_roster_ack",
    input=CaseInput(
        path_params={"space_id": 1},
        headers={"x-user-id": _ACTOR},
        query_params={"actor_staff_no": "forged-actor"},
        json_body={
            "request_id": "endpoint-intent",
            "name": _STATE["name"],
            "description": _STATE["description"],
        },
    ),
    seed=_seed_space,
    expect=ExpectSuccess(status=202, json_equals=_STATE),
    extra_assertions=(_assert_create,),
)
def create_member_success():
    pass


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="outsider_cannot_create",
    input=CaseInput(
        path_params={"space_id": 1},
        headers={"x-user-id": "outsider"},
        json_body={"request_id": "endpoint-intent", "name": _STATE["name"]},
    ),
    seed=_seed_space,
    expect=ExpectError(
        status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
    ),
    extra_assertions=(_assert_no_provider_call,),
)
def create_outsider_error():
    pass


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="member_lists_with_cursor",
    input=CaseInput(
        path_params={"space_id": 1},
        headers={"x-user-id": _ACTOR},
        query_params={
            "page_size": 2,
            "cursor": "kb-before",
            "actor_staff_no": "forged-actor",
        },
    ),
    seed=_seed_knowledge,
    expect=ExpectSuccess(
        json_equals={"knowledge_bases": [_STATE], "next_cursor": None}
    ),
    extra_assertions=(_assert_list,),
)
def list_member_success():
    pass


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="outsider_cannot_list",
    input=CaseInput(path_params={"space_id": 1}, headers={"x-user-id": "outsider"}),
    seed=_seed_knowledge,
    expect=ExpectError(
        status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
    ),
    extra_assertions=(_assert_no_provider_call,),
)
def list_outsider_error():
    pass


@endpoint_test(
    method="GET",
    path=_PATH + "/{kb_id}",
    scenario="member_reads_detail",
    input=CaseInput(
        path_params={"space_id": 1, "kb_id": "kb-endpoint"},
        headers={"x-user-id": _ACTOR},
        query_params={"actor_staff_no": "forged-actor"},
    ),
    seed=_seed_knowledge,
    expect=ExpectSuccess(json_equals=_STATE),
    extra_assertions=(_assert_get,),
)
def detail_member_success():
    pass


@endpoint_test(
    method="GET",
    path=_PATH + "/{kb_id}",
    scenario="outsider_cannot_read_detail",
    input=CaseInput(
        path_params={"space_id": 1, "kb_id": "kb-endpoint"},
        headers={"x-user-id": "outsider"},
    ),
    seed=_seed_knowledge,
    expect=ExpectError(
        status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
    ),
    extra_assertions=(_assert_no_provider_call,),
)
def detail_outsider_error():
    pass
