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
