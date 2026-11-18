"""Lifecycle metadata survives the actual catalog/service/HTTP provider chain."""

from contextlib import contextmanager
from typing import Annotated

import httpx
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.di.config import TaskQueueConfig
from agentclaw.community.di.space_knowledge_capture_config import (
    SpaceKnowledgeCaptureConfig,
)
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeSync
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.plugin_api.http_client import HttpClient

from tests.community.endpoints.test_knowledge_management import (
    ACTOR,
    _assert_real_composition,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.knowledge_management.http_contract import detail


class CatalogWire(httpx.MockTransport):
    def __init__(self, rows):
        self.rows, self.calls = rows, []
        super().__init__(self.answer)

    def answer(self, request):
        self.calls.append(request)
        assert request.method == "GET"
        assert request.url.path == (
            "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases"
        )
        assert dict(request.url.params) == {
            "actor_staff_no": ACTOR,
            "cursor": "previous-page",
            "limit": "2",
        }
        return httpx.Response(
            200, json={"items": self.rows, "next_cursor": "next-page"}
        )


class AcceptingMembershipSink:
    def is_enabled(self):
        return True

    def apply_snapshot(self, snapshot):
        pass


def catalog_seed(lifecycle, *, enabled=True):
    def seed(world):
        world.injector.binder.bind(
            KnowledgeIntegrationConfig,
            to=KnowledgeIntegrationConfig(True, "https://knowledge.test"),
        )
        world.injector.binder.bind(
            KnowledgeManagementConfig, to=KnowledgeManagementConfig(True, "teamclaw")
        )
        if enabled:
            world.injector.binder.bind(
                SpaceKnowledgeCaptureConfig,
                to=SpaceKnowledgeCaptureConfig(enabled=True),
            )
            world.injector.binder.bind(
                SpaceMembershipSink, to=AcceptingMembershipSink()
            )
            # Select the enabled capability using the existing DI provider, not a
            # hand-built synchronizer. Public defaults deliberately stay closed.
            world.injector.binder.bind(
                SpaceKnowledgeSync, to=world.get(SpaceMembershipSynchronizer)
            )
        world.get(SpaceServiceProtocol).create_team(
            name="Knowledge team", creator_id=ACTOR
        )
        make_staff_user(world, user_id=ACTOR)
        if enabled:
            world.get(SpaceKnowledgeSync).ensure_synced(
                space_id=1, environment=get_current_env()
            )
        # Include a READ-only row: cancellation capability is not can_manage.
        wire = CatalogWire(
            [
                detail(kb="kb", **lifecycle),
                detail(
                    kb="read-only",
                    can_manage=False,
                    **(lifecycle | {"can_cancel_delete": False} if lifecycle else {}),
                ),
            ]
        )
        world.injector.binder.bind(CatalogWire, to=wire)

        @contextmanager
        def send(method, url, **kwargs):
            kwargs.pop("timeout", None)
            response = wire.handle_request(httpx.Request(method, url, **kwargs))
            try:
                yield response
            finally:
                response.close()

        world.get(Annotated[HttpClient, "knowledge"]).set_override("stream", send)

    return seed


def lifecycle_page(response, world):
    _assert_real_composition(response, world)
    sync = world.get(SpaceMembershipSynchronizer)
    assert world.get(KnowledgeManagementServiceProtocol).sync is sync
    assert world.get(SpaceKnowledgeSync) is sync
    assert sync._app == world.get(TaskQueueConfig).app
    wire = world.get(CatalogWire)
    assert len(wire.calls) == 1
    body = response.json()
    assert body["items"] == body["knowledge_bases"]
    for result, source in zip(body["items"], wire.rows, strict=True):
        assert result == {**source, "space_name": result["space_name"]}


for scenario, lifecycle in [
    ("active", {"status": "active", "delete_at_ms": None, "can_cancel_delete": False}),
    (
        "pending_cancellable",
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": True,
        },
    ),
    (
        "pending_not_cancellable",
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": False,
        },
    ),
    ("legacy_omitted", {}),
]:
    endpoint_test(
        method="GET",
        path="/api/knowledge/spaces/{space_id}/knowledge-bases",
        scenario="catalog_lifecycle_" + scenario,
        input=CaseInput(
            path_params={"space_id": 1},
            query_params={"cursor": "previous-page", "limit": "2"},
            headers={"x-user-id": ACTOR},
        ),
        seed=catalog_seed(lifecycle),
        expect=ExpectSuccess(status=200, json_contains={"next_cursor": "next-page"}),
        extra_assertions=(lifecycle_page,),
    )(lambda: None)


def closed_profile(response, world):
    _assert_real_composition(response, world)
    assert world.get(KnowledgeManagementServiceProtocol).sync is world.get(
        SpaceKnowledgeSync
    )
    assert not isinstance(world.get(SpaceKnowledgeSync), SpaceMembershipSynchronizer)
    assert not world.get(CatalogWire).calls


@endpoint_test(
    method="GET",
    path="/api/knowledge/spaces/{space_id}/knowledge-bases",
    scenario="catalog_lifecycle_default_closed",
    input=CaseInput(path_params={"space_id": 1}, headers={"x-user-id": ACTOR}),
    seed=catalog_seed({}, enabled=False),
    expect=ExpectError(
        status=503, json_contains={"detail": {"code": "SPACE_SYNC_UNAVAILABLE"}}
    ),
    extra_assertions=(closed_profile,),
)
def default_sync_profile_remains_closed():
    pass
