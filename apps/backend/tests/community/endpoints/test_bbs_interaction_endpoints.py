"""Endpoint-framework coverage for BBS interactions and personal-center activity."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.task.forum_interaction_service import (
    ForumInteractionServiceProtocol,
)
from agentclaw.community.api.task.forum_personal_center_activity_service import (
    ForumPersonalCenterActivityServiceProtocol,
)
from agentclaw.community.api.task.forum_service import ForumServiceProtocol
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionRecord,
    ForumInteractionTargetType,
    ForumInteractionSummaryResult,
    ForumStatsRecord,
    ForumSummaryScope,
    PersonalCenterActivityPage,
    PersonalCenterActivityRecord,
    PersonalCenterActivityType,
    PersonalCenterReplyContext,
    PersonalCenterTopicContext,
)
from agentclaw.community.core.task.forum.models import ForumPostRecord
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_CALLER = "interaction-caller"
_KEY = "bbs-interaction-framework-signing-key-32b"
_NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _CALLER,
                        "username": "interaction@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _interaction_record() -> ForumInteractionRecord:
    return ForumInteractionRecord(
        actor_type=ForumActorType.HUMAN,
        actor_id=_CALLER,
        action_type=ForumInteractionActionType.LIKE,
        target_type=ForumInteractionTargetType.TOPIC,
        target_id="topic_endpoint_1",
        env="test",
        active=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_create_interaction(world):
    def record_interaction(self, *_args, **_kwargs):
        return _interaction_record()

    bind_overrides(
        world,
        ForumInteractionServiceProtocol,
        {"record_interaction": record_interaction},
    )


def _seed_summaries(world):
    def get_summary(self, *_args, **_kwargs):
        return ForumInteractionSummaryResult(
            scope=ForumSummaryScope.TARGET,
            target_type=ForumInteractionTargetType.TOPIC,
            target_id="topic_endpoint_1",
            stats=ForumStatsRecord(like_count=2),
        )

    def get_summary_batch(self, *_args, **_kwargs):
        return (
            ForumInteractionSummaryResult(
                scope=ForumSummaryScope.TARGET,
                target_type=ForumInteractionTargetType.TOPIC,
                target_id="topic_endpoint_1",
                stats=ForumStatsRecord(like_count=2),
            ),
        )

    bind_overrides(
        world,
        ForumInteractionServiceProtocol,
        {"get_summary": get_summary, "get_summary_batch": get_summary_batch},
    )


def _activity_page() -> PersonalCenterActivityPage:
    topic_context = PersonalCenterTopicContext(
        title="Topic title",
        body_preview="Topic body",
        topic_type="discussion",
        author_type=ForumActorType.HUMAN,
        author_id=_CALLER,
        author_display_name="Interaction Caller",
        reply_count=2,
        like_count=3,
        latest_reply_at=_NOW,
    )
    return PersonalCenterActivityPage(
        total=2,
        items=(
            PersonalCenterActivityRecord(
                activity_type=PersonalCenterActivityType.VIEWED,
                actor_type=ForumActorType.HUMAN,
                actor_id=_CALLER,
                target_type=ForumInteractionTargetType.TOPIC,
                target_id="topic_endpoint_1",
                topic_id="topic_endpoint_1",
                happened_at=_NOW,
                body_preview="Topic title",
                topic_context=topic_context,
            ),
            PersonalCenterActivityRecord(
                activity_type=PersonalCenterActivityType.REPLIED,
                actor_type=ForumActorType.HUMAN,
                actor_id=_CALLER,
                target_type=ForumInteractionTargetType.POST,
                target_id="post_parent_1",
                topic_id="topic_endpoint_1",
                post_id="post_child_1",
                happened_at=_NOW,
                body_preview="My nested reply",
                topic_context=topic_context,
                replied_to_post=PersonalCenterReplyContext(
                    post_id="post_parent_1",
                    author_type=ForumActorType.BOT,
                    author_id="bot-1",
                    author_display_name="Responder Bot",
                    body_preview="Original reply",
                ),
            ),
        ),
    )


def _seed_personal_center(world):
    def get_personal_center_activities(self, *_args, **_kwargs):
        return _activity_page()

    bind_overrides(
        world,
        ForumPersonalCenterActivityServiceProtocol,
        {"get_personal_center_activities": get_personal_center_activities},
    )


def _followed_activity_page() -> PersonalCenterActivityPage:
    return PersonalCenterActivityPage(
        total=1,
        items=(
            PersonalCenterActivityRecord(
                activity_type=PersonalCenterActivityType.FOLLOWED,
                actor_type=ForumActorType.HUMAN,
                actor_id=_CALLER,
                target_type=ForumInteractionTargetType.HUMAN,
                target_id="user-b",
                topic_id=None,
                happened_at=_NOW,
                body_preview="User B",
            ),
        ),
    )


def _seed_personal_center_followed(world):
    def get_personal_center_activities(self, *_args, **_kwargs):
        return _followed_activity_page()

    bind_overrides(
        world,
        ForumPersonalCenterActivityServiceProtocol,
        {"get_personal_center_activities": get_personal_center_activities},
    )


def _seed_helpful(world):
    def set_post_helpful(self, **_kwargs):
        return ForumPostRecord(
            post_id="post_endpoint_1",
            topic_id="topic_endpoint_1",
            author_type="HUMAN",
            author_id=_CALLER,
            body="Helpful answer",
            helpful=True,
            created_at=_NOW,
            updated_at=_NOW,
        )

    bind_overrides(
        world,
        ForumServiceProtocol,
        {"set_post_helpful": set_post_helpful},
    )


def _assert_personal_center_projection(response, _world):
    items = response.json()["data"]["items"]
    viewed = next(
        item
        for item in items
        if item["activity_type"] == "VIEWED"
    )
    nested_reply = next(
        item
        for item in items
        if item["activity_type"] == "REPLIED"
    )
    assert viewed["topic_context"]["title"] == "Topic title"
    assert viewed["topic_context"]["body_preview"] == "Topic body"
    assert viewed["topic_context"]["reply_count"] == 2
    assert viewed["topic_context"]["like_count"] == 3
    assert viewed["topic_context"]["latest_reply_at"] is not None
    assert nested_reply["replied_to_post"]["post_id"] == "post_parent_1"
    assert nested_reply["replied_to_post"]["author_display_name"] == "Responder Bot"
    assert nested_reply["replied_to_post"]["body_preview"] == "Original reply"


_CREATE_BODY = {
    "actor_type": "HUMAN",
    "actor_id": _CALLER,
    "action_type": "LIKE",
    "target_type": "TOPIC",
    "target_id": "topic_endpoint_1",
    "active": 1,
}
_BATCH_BODY = {
    "items": [
        {
            "actor_type": "HUMAN",
            "actor_id": _CALLER,
            "target_type": "TOPIC",
            "target_id": "topic_endpoint_1",
        }
    ]
}
_HELPFUL_BODY = {"author_type": "HUMAN", "author_id": _CALLER, "helpful": True}


# ---------------------------------------------------------------------------
# Public gateway-signed surface
# ---------------------------------------------------------------------------
@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/interactions",
    scenario="created",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        json_body=_CREATE_BODY,
    ),
    seed=lambda world: (_boot_verifier(world), _seed_create_interaction(world)),
    expect=ExpectSuccess(status=201),
)
def public_create_interaction_ok():
    """Upsert one interaction state and return 201."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/interactions",
    scenario="invalid_body",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        json_body={"actor_type": "HUMAN"},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_create_interaction_invalid_body():
    """Reject a create request without its complete actor/action/target tuple."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/interactions",
    scenario="invalid_active",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        json_body={**_CREATE_BODY, "active": True},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_create_interaction_rejects_boolean_active():
    """Reject the former boolean active contract; only +1 and -1 are valid."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/interactions/summary",
    scenario="target_summary",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"target_type": "TOPIC", "target_id": "topic_endpoint_1"},
    ),
    seed=lambda world: (_boot_verifier(world), _seed_summaries(world)),
    expect=ExpectSuccess(status=200),
)
def public_get_summary_ok():
    """Read one target summary and perform no interaction write."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/interactions/summary",
    scenario="legacy_viewer_parameter",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={
            "target_type": "TOPIC",
            "target_id": "topic_endpoint_1",
            "viewer_author_id": _CALLER,
        },
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=400),
)
def public_get_summary_rejects_legacy_viewer_query():
    """Reject the removed viewer query as an unknown parameter."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/interactions/summary/batch",
    scenario="batch",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        json_body=_BATCH_BODY,
    ),
    seed=lambda world: (_boot_verifier(world), _seed_summaries(world)),
    expect=ExpectSuccess(status=200),
)
def public_get_summary_batch_ok():
    """Read a mixed batch and preserve each requested summary dimension."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/interactions/summary/batch",
    scenario="empty_items",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        json_body={"items": []},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=400),
)
def public_get_summary_batch_empty_items():
    """Reject an empty batch instead of returning an ambiguous empty result."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="own_activities",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={
            "owner_user_id": _CALLER,
            "actor_scope": "ALL",
            "activity_type": "ALL",
            "target_type": "ALL",
            "page": 1,
            "page_size": 20,
        },
    ),
    seed=lambda world: (_boot_verifier(world), _seed_personal_center(world)),
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_personal_center_projection,),
)
def public_get_personal_center_activities_ok():
    """Read the signed-in owner's server-paged BBS activity projection."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="invalid_target_filter",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={
            "owner_user_id": _CALLER,
            "activity_type": "LIKED",
            "target_type": "NOPE",
            "page": 1,
            "page_size": 20,
        },
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="followed_activities",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={
            "owner_user_id": _CALLER,
            "activity_type": "FOLLOWED",
            "target_type": "HUMAN",
            "page": 1,
            "page_size": 20,
        },
    ),
    seed=lambda world: (
        _boot_verifier(world),
        _seed_personal_center_followed(world),
    ),
    expect=ExpectSuccess(status=200),
)
def public_get_personal_center_followed_activities_ok():
    """Serialize one identity follow without requiring a Topic context."""
def public_get_personal_center_activities_invalid_target_filter():
    """Reject target values outside the Personal Center schema."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="owner_mismatch",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"owner_user_id": "other-user", "page": 1, "page_size": 20},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=403),
)
def public_get_personal_center_activities_owner_mismatch():
    """Refuse to project another owner's activity through the public route."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/helpful",
    scenario="accepted_help_reply",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        json_body=_HELPFUL_BODY,
    ),
    seed=lambda world: (_boot_verifier(world), _seed_helpful(world)),
    expect=ExpectSuccess(status=200),
)
def public_set_helpful_ok():
    """Mark one HELP reply helpful and atomically append its ACCEPT event."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/helpful",
    scenario="invalid_body",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        json_body={"author_type": "HUMAN"},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_set_helpful_invalid_body():
    """Reject a helpful update that omits the author id and state."""


# ---------------------------------------------------------------------------
# Trusted internal mirror surface
# ---------------------------------------------------------------------------
@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/interactions",
    scenario="created",
    input=CaseInput(json_body=_CREATE_BODY),
    seed=_seed_create_interaction,
    expect=ExpectSuccess(status=201),
)
def internal_create_interaction_ok():
    """Mirror the public interaction state write without gateway auth."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/interactions",
    scenario="invalid_body",
    input=CaseInput(json_body={"actor_type": "HUMAN"}),
    expect=ExpectError(status=422),
)
def internal_create_interaction_invalid_body():
    """Mirror public schema validation for the internal service surface."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/interactions/summary",
    scenario="target_summary",
    input=CaseInput(
        query_params={"target_type": "TOPIC", "target_id": "topic_endpoint_1"}
    ),
    seed=_seed_summaries,
    expect=ExpectSuccess(status=200),
)
def internal_get_summary_ok():
    """Mirror the public single-summary read without gateway auth."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/interactions/summary",
    scenario="legacy_viewer_parameter",
    input=CaseInput(
        query_params={
            "target_type": "TOPIC",
            "target_id": "topic_endpoint_1",
            "viewer_author_id": _CALLER,
        }
    ),
    expect=ExpectError(status=400),
)
def internal_get_summary_rejects_legacy_viewer_query():
    """Mirror the public unknown-query rejection."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/interactions/summary/batch",
    scenario="batch",
    input=CaseInput(json_body=_BATCH_BODY),
    seed=_seed_summaries,
    expect=ExpectSuccess(status=200),
)
def internal_get_summary_batch_ok():
    """Mirror the public mixed-batch summary read."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/interactions/summary/batch",
    scenario="empty_items",
    input=CaseInput(json_body={"items": []}),
    expect=ExpectError(status=400),
)
def internal_get_summary_batch_empty_items():
    """Mirror the public empty-batch rejection."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="own_activities",
    input=CaseInput(
        query_params={
            "owner_user_id": _CALLER,
            "actor_scope": "ALL",
            "activity_type": "ALL",
            "target_type": "ALL",
            "page": 1,
            "page_size": 20,
        }
    ),
    seed=_seed_personal_center,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_personal_center_projection,),
)
def internal_get_personal_center_activities_ok():
    """Mirror the trusted owner-scoped activity projection."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="invalid_target_filter",
    input=CaseInput(
        query_params={
            "owner_user_id": _CALLER,
            "activity_type": "LIKED",
            "target_type": "NOPE",
            "page": 1,
            "page_size": 20,
        }
    ),
    expect=ExpectError(status=422),
)


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="followed_activities",
    input=CaseInput(
        query_params={
            "owner_user_id": _CALLER,
            "activity_type": "FOLLOWED",
            "target_type": "HUMAN",
            "page": 1,
            "page_size": 20,
        }
    ),
    seed=_seed_personal_center_followed,
    expect=ExpectSuccess(status=200),
)
def internal_get_personal_center_followed_activities_ok():
    """Mirror identity-follow serialization without a Topic context."""
def internal_get_personal_center_activities_invalid_target_filter():
    """Mirror the Personal Center target filter schema validation."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/personal-center/activities",
    scenario="invalid_owner",
    input=CaseInput(query_params={"owner_user_id": "", "page": 1, "page_size": 20}),
    expect=ExpectError(status=422),
)
def internal_get_personal_center_activities_invalid_owner():
    """Mirror the explicit owner-id schema validation."""


@endpoint_test(
    method="PUT",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/helpful",
    scenario="accepted_help_reply",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        json_body=_HELPFUL_BODY,
    ),
    seed=_seed_helpful,
    expect=ExpectSuccess(status=200),
)
def internal_set_helpful_ok():
    """Mirror the atomic helpful and ACCEPT state update."""
