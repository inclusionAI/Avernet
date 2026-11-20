"""Endpoint coverage for the bot-message-feedback router.

Registers happy + error cases for the feedback submission route. Cases use
real DI-backed services and the in-memory SQLite database; local-mode auth
reads ``x-user-id`` to mint the caller identity.
"""
from __future__ import annotations

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_FEEDBACK_PATH = "/api/v1/bot-message-feedback/{message_id}"


@endpoint_test(
    method="POST",
    path=_FEEDBACK_PATH,
    scenario="happy",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        path_params={"message_id": "msg-1"},
        json_body={
            "feedback_type": "dislike",
            "message_content": "answer",
            "user_message_id": "user-msg-1",
            "user_message_content": "question",
            "session_key": "session-1",
            "bot_id": "bot-1",
            "reason": "inaccurate",
            "comment": "not helpful",
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "message_id": "msg-1",
                "feedback_type": "dislike",
                "bot_id": "bot-1",
                "user_id": "u1",
            },
        },
    ),
)
def submit_feedback_happy():
    """Successful feedback submission persists a record."""


@endpoint_test(
    method="POST",
    path=_FEEDBACK_PATH,
    scenario="validation_error",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        path_params={"message_id": "msg-1"},
        json_body={
            "feedback_type": "dislike",
            # Missing required fields => FastAPI validation failure.
        },
    ),
    expect=ExpectError(status=422),
)
def submit_feedback_validation_error():
    """Invalid request body is rejected with 422 before reaching the service."""


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback",
    scenario="happy_empty_batch",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        query_params={"message_ids": "msg-1,msg-2"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": []},
    ),
)
def list_feedback_empty_batch():
    """Reading absent feedback returns an empty list, not 404."""


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback/{message_id}",
    scenario="happy_absent",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        path_params={"message_id": "msg-1"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": None},
    ),
)
def get_feedback_absent():
    """Reading one message without feedback returns null data."""


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback",
    scenario="missing_message_ids",
    input=CaseInput(headers={"x-user-id": "u1"}),
    expect=ExpectError(status=422),
)
def list_feedback_missing_message_ids():
    """The batch route requires at least one message id."""


def _seed_and_fail_feedback_read(world) -> None:
    from agentclaw.community.api.bot_message_feedback_service import (
        BotMessageFeedbackServiceProtocol,
    )
    from tests.community.framework.di_seams import bind_failing_method

    bind_failing_method(
        world,
        BotMessageFeedbackServiceProtocol,
        "get_feedback",
        RuntimeError("feedback store unavailable"),
    )


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback/{message_id}",
    scenario="unexpected_error",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        path_params={"message_id": "msg-1"},
    ),
    seed=_seed_and_fail_feedback_read,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 5999},
    ),
)
def get_feedback_unexpected_error():
    """A storage failure is reported as the 5999 error envelope."""


def _seed_mixed_feedback_rows(world) -> None:
    """Seed one visible row plus rows that must stay hidden.

    u1/dev/msg-1 is the only row the read endpoints may surface for u1 in dev;
    msg-2 belongs to the same user but another env, msg-3 to another user.
    """
    from agentclaw.community.core.bot_message_feedback.models import (
        AcBotMessageFeedback,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin

    rows = [
        {
            "message_id": "msg-1",
            "user_id": "u1",
            "env": "dev",
            "feedback_type": "like",
        },
        {
            "message_id": "msg-2",
            "user_id": "u1",
            "env": "pre",
            "feedback_type": "dislike",
            "reason": "inaccurate",
        },
        {
            "message_id": "msg-3",
            "user_id": "u2",
            "env": "dev",
            "feedback_type": "dislike",
        },
    ]
    with world.get(DatabasePlugin).orm_session() as session:
        for row in rows:
            session.add(
                AcBotMessageFeedback(
                    message_content=f"answer for {row['message_id']}",
                    user_message_id=f"user-{row['message_id']}",
                    user_message_content="question",
                    bot_id="bot-1",
                    **row,
                )
            )


def _assert_only_visible_feedback(response, world) -> None:
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["message_id"] == "msg-1"
    assert data[0]["user_id"] == "u1"
    assert data[0]["env"] == "dev"
    assert data[0]["feedback_type"] == "like"


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback",
    scenario="happy_isolated_rows",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        query_params={"message_ids": "msg-1,msg-2,msg-3"},
    ),
    seed=_seed_mixed_feedback_rows,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [
                {
                    "message_id": "msg-1",
                    "user_id": "u1",
                    "env": "dev",
                    "feedback_type": "like",
                }
            ],
        },
    ),
    extra_assertions=(_assert_only_visible_feedback,),
)
def list_feedback_isolates_user_and_env():
    """A history read only returns this user's rows in the current env."""


@endpoint_test(
    method="GET",
    path="/api/v1/bot-message-feedback/{message_id}",
    scenario="happy_owned_row",
    input=CaseInput(
        headers={"x-user-id": "u1"},
        path_params={"message_id": "msg-1"},
    ),
    seed=_seed_mixed_feedback_rows,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "message_id": "msg-1",
                "user_id": "u1",
                "env": "dev",
                "feedback_type": "like",
            },
        },
    ),
)
def get_feedback_returns_owned_row():
    """A single read returns the stored like for the current user."""
