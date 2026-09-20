"""Real publish-ignore endpoint invocation; only remote Engine I/O is simulated."""

from tests.community.factories.publish_ignore import (
    seed_publish_ignore,
    assert_ignore_engine_called,
    seed_publish_ignore_query,
    assert_ignore_query_called,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/service-bot/publish/ops/publish-ignore"
_INPUT = CaseInput(
    headers={"x-user-id": "ignore_owner"},
    json_body={
        "bot_id": "ignore-bot",
        "entity_id": "ignore_owner",
        "stage": "online",
        "operation": "add",
        "path": "workspace/cache",
    },
)


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy",
    input=_INPUT,
    seed=seed_publish_ignore,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(assert_ignore_engine_called,),
)
def publish_ignore_happy():
    """Authorized owner updates the selected release's pinned replica."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="engine_rejected",
    input=_INPUT,
    seed=lambda world: seed_publish_ignore(world, engine_success=False),
    expect=ExpectError(status=200, json_contains={"success": False}),
    extra_assertions=(assert_ignore_engine_called,),
)
def publish_ignore_engine_rejected():
    """Engine refusal remains a failed result after real target resolution."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="draft_workspace",
    input=CaseInput(
        headers={"x-user-id": "ignore_owner"},
        json_body={
            "bot_id": "ignore-bot", "entity_id": "ignore_owner",
            "stage": "draft", "operation": "add", "path": "workspace/cache",
        },
    ),
    seed=lambda world: seed_publish_ignore(world, stage="draft"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(assert_ignore_engine_called,),
)
def publish_ignore_draft_workspace():
    """Owner updates the draft source binding without ext.binding.draft."""


@endpoint_test(
    method="GET", path=_PATH, scenario="happy",
    input=CaseInput(headers={"x-user-id": "ignore_owner"}, query_params={
        "bot_id": "ignore-bot", "entity_id": "ignore_owner", "stage": "draft",
    }),
    seed=lambda world: seed_publish_ignore_query(world, stage="draft"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(assert_ignore_query_called,),
)
def publish_ignore_query_happy():
    """Read draft rules using real authorization and current binding resolution."""


@endpoint_test(
    method="GET", path=_PATH, scenario="engine_rejected",
    input=CaseInput(headers={"x-user-id": "ignore_owner"}, query_params={
        "bot_id": "ignore-bot", "entity_id": "ignore_owner", "stage": "online",
    }),
    seed=lambda world: seed_publish_ignore_query(world, engine_success=False),
    expect=ExpectError(status=200, json_contains={"success": False}),
    extra_assertions=(assert_ignore_query_called,),
)
def publish_ignore_query_engine_rejected():
    """A failed read is not presented as an empty successful list."""
